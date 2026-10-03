from django.core.cache import cache
from django.db.models import Count, Q

from .models import Notification, School, User


NOTIFICATION_CONTEXT_TIMEOUT = 120  # 2 minutes (was 10s — caused constant cache misses)


def _notification_cache_key(user_id):
    return f"dashboard-notifications:{user_id}"


def invalidate_notification_cache(user_id):
    """Call this after creating or marking notifications to bust the cache."""
    cache.delete(_notification_cache_key(user_id))


def _school_for_request(request):
    """Return the school unambiguously associated with this request.
    
    Optimized: reuses TenantMiddleware's resolved school to avoid
    duplicate profile/school lookups.
    """
    if not request.user.is_authenticated:
        return getattr(request, "school", None)

    # TenantMiddleware has already validated this host/user pairing. Reuse its
    # resolved object instead of repeating profile and school lookups.
    tenant_school = getattr(request, "school", None)
    if tenant_school is not None:
        return tenant_school

    selected_id = request.session.get("selected_school_id")
    if request.user.is_superuser and selected_id:
        return School.objects.filter(pk=selected_id, is_active=True).first()
    if request.user.is_superuser:
        return None

    # Use role-based lookup (only check the ONE matching profile)
    role = request.user.role
    if role == User.Role.PARENT:
        relation = "guardian_profile"
    elif role == User.Role.STUDENT:
        relation = "student_profile"
    else:
        relation = "staff_profile"
    try:
        school_id = getattr(request.user, relation).school_id
    except Exception:
        return None
    return School.objects.filter(pk=school_id, is_active=True).first()


def dashboard_notifications(request):
    school = _school_for_request(request)
    if not request.user.is_authenticated:
        return {
            "dashboard_notifications": [],
            "unread_notification_count": 0,
            "dashboard_school": None,
            "login_school": school,
        }
    cache_key = _notification_cache_key(request.user.pk)
    cached = cache.get(cache_key)
    if cached is None:
        # Single query with annotation instead of two separate queries
        qs = Notification.objects.filter(
            recipient_id=request.user.pk
        ).only("id", "title", "message", "is_read", "created_at").order_by("-created_at")[:8]
        rows = list(qs)
        unread = sum(1 for r in rows if not r.is_read)
        # If all 8 are read, we might have more unread ones not in this slice.
        # Only run the count query if needed.
        if unread == 0 and rows:
            unread = Notification.objects.filter(
                recipient_id=request.user.pk, is_read=False
            ).count()
        cached = {
            "notifications": rows,
            "unread_notification_count": unread,
        }
        cache.set(cache_key, cached, timeout=NOTIFICATION_CONTEXT_TIMEOUT)

    return {
        "dashboard_notifications": cached["notifications"],
        "unread_notification_count": cached["unread_notification_count"],
        "dashboard_school": school,
        "login_school": None,
    }
