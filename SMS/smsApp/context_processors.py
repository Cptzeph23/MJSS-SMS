from django.core.cache import cache

from .models import Notification, School


NOTIFICATION_CONTEXT_TIMEOUT = 5


def _notification_cache_key(user_id):
    return f"dashboard-notifications:{user_id}"


def _school_for_request(request):
    """Return the school unambiguously associated with this request."""
    if not request.user.is_authenticated:
        return getattr(request, "school", None)

    selected_id = request.session.get("selected_school_id")
    if request.user.is_superuser and selected_id:
        return School.objects.filter(pk=selected_id, is_active=True).first()
    if request.user.is_superuser:
        return None

    school_ids = set()
    for relation in ("staff_profile", "student_profile", "guardian_profile"):
        try:
            profile = getattr(request.user, relation)
        except Exception:
            profile = None
        if profile is not None and profile.school_id:
            school_ids.add(profile.school_id)
    if len(school_ids) != 1:
        return None
    return School.objects.filter(pk=school_ids.pop(), is_active=True).first()


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
        rows = list(
            Notification.objects.filter(recipient_id=request.user.pk)
            .only("id", "title", "message", "is_read", "created_at")
            .order_by("-created_at")[:8]
        )
        cached = {
            "notifications": rows,
            "unread_notification_count": Notification.objects.filter(
                recipient_id=request.user.pk, is_read=False
            ).count(),
        }
        cache.set(cache_key, cached, timeout=NOTIFICATION_CONTEXT_TIMEOUT)

    return {
        "dashboard_notifications": cached["notifications"],
        "unread_notification_count": cached["unread_notification_count"],
        "dashboard_school": school,
        "login_school": None,
    }
