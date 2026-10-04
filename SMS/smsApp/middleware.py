from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.utils import timezone
from django.http import HttpResponseNotFound

from .models import RESERVED_SCHOOL_SUBDOMAINS, School, User


_RESERVED_SUBDOMAINS = RESERVED_SCHOOL_SUBDOMAINS

_TENANT_CACHE_TIMEOUT = 300  # 5 minutes


def _user_school_ids(user):
    """Return school IDs for the user based on their role.
    
    Optimized: checks only the ONE profile relation matching the user's
    role instead of probing all three (which caused 3 DB queries per request).
    """
    if not user.is_authenticated or user.is_superuser:
        return set()
    role = user.role
    # Map role to the single profile relation that can exist
    if role in {User.Role.PARENT}:
        relation = "guardian_profile"
    elif role in {User.Role.STUDENT}:
        relation = "student_profile"
    else:
        relation = "staff_profile"
    try:
        profile = getattr(user, relation)
    except Exception:
        return set()
    if profile is not None and profile.school_id:
        return {profile.school_id}
    return set()


def user_can_access_school(user, school):
    """Return whether an authenticated user may enter this tenant."""
    return bool(
        user is not None
        and user.is_authenticated
        and not getattr(user, "is_locked", False)
        and user.is_active
        and (user.is_superuser or school.pk in _user_school_ids(user))
    )


def _get_school_for_subdomain(subdomain):
    """Cached school lookup by subdomain. Avoids hitting DB on every request."""
    cache_key = f"tenant:subdomain:{subdomain}"
    school = cache.get(cache_key)
    if school is None:
        school = School.objects.filter(
            subdomain=subdomain, is_active=True
        ).first()
        # Cache even None results (as False sentinel) to avoid repeated misses
        cache.set(cache_key, school if school is not None else False, timeout=_TENANT_CACHE_TIMEOUT)
    elif school is False:
        school = None
    return school


class TenantMiddleware:
    """Resolve an active school from a configured hostname.

    The middleware is intentionally host-based: query parameters and form
    fields cannot change request.school. Unknown school subdomains return a
    generic 404 and never fall back to another school.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        root = getattr(settings, "TENANT_ROOT_DOMAIN", "") or ""
        self.root_domain = root.lower().strip().lstrip(".").rstrip(".")

    def __call__(self, request):
        request.school = None
        request.tenant_subdomain = None
        request.tenant_host_error = False

        if self.root_domain:
            host = urlsplit("//" + request.get_host()).hostname
            host = (host or "").lower().rstrip(".")
            root = self.root_domain
            if host.endswith("." + root):
                subdomain = host[: -(len(root) + 1)]
                request.tenant_subdomain = subdomain
                if (
                    "." in subdomain
                    or subdomain in _RESERVED_SUBDOMAINS
                    or not subdomain
                ):
                    request.tenant_host_error = True
                else:
                    request.school = _get_school_for_subdomain(subdomain)
                    if request.school is None:
                        request.tenant_host_error = True

        if request.tenant_host_error:
            return HttpResponseNotFound("Not found.")

        if request.school is not None and user_can_access_school(
            getattr(request, "user", None), request.school
        ):
            pass
        elif request.school is not None and getattr(
            getattr(request, "user", None), "is_authenticated", False
        ):
            return HttpResponseNotFound("Not found.")

        return self.get_response(request)


class AccountSecurityMiddleware:
    """Enforce temporary-password rotation and short browser idle sessions."""

    IDLE_TIMEOUT_SECONDS = 300
    WARNING_GRACE_SECONDS = 10

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = request.path_info
        if user is None or not user.is_authenticated or path.startswith("/api/"):
            return self.get_response(request)

        allowed_during_password_change = {
            "/login/", "/logout/", "/account/password/change/",
        }
        if user.must_change_password and path not in allowed_during_password_change:
            return redirect("dashboard:password_change")

        session_key = request.session.session_key
        if session_key:
            cache_key = f"browser-idle:{session_key}"
            last_seen = cache.get(cache_key)
            now = timezone.now().timestamp()
            # Keep the timestamp for the session's full lifetime so an old
            # timestamp remains detectable after the five-minute idle window.
            timeout = getattr(settings, "SESSION_COOKIE_AGE", 1209600)
            is_keepalive = path == "/session/keep-alive/"
            is_background_poll = request.headers.get("X-Requested-With") == "XMLHttpRequest" and not is_keepalive
            if last_seen and now - last_seen > self.IDLE_TIMEOUT_SECONDS + self.WARNING_GRACE_SECONDS:
                cache.delete(cache_key)
                logout(request)
                return redirect("dashboard:login")
            if not is_background_poll:
                cache.set(cache_key, now, timeout=timeout)
        return self.get_response(request)
