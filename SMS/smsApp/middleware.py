from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponseNotFound

from .models import RESERVED_SCHOOL_SUBDOMAINS, School


_RESERVED_SUBDOMAINS = RESERVED_SCHOOL_SUBDOMAINS


def _user_school_ids(user):
    if not user.is_authenticated or user.is_superuser:
        return set()
    school_ids = set()
    for relation in ("staff_profile", "student_profile", "guardian_profile"):
        try:
            profile = getattr(user, relation)
        except Exception:
            profile = None
        if profile is not None and profile.school_id:
            school_ids.add(profile.school_id)
    return school_ids


def user_can_access_school(user, school):
    """Return whether an authenticated user may enter this tenant."""
    return bool(
        user is not None
        and user.is_authenticated
        and not getattr(user, "is_locked", False)
        and user.is_active
        and (user.is_superuser or school.pk in _user_school_ids(user))
    )


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
                    request.school = School.objects.filter(
                        subdomain=subdomain, is_active=True
                    ).first()
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
