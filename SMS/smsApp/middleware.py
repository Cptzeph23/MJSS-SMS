from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
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
