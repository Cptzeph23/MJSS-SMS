from django.core.cache import cache

from .models import Notification


NOTIFICATION_CONTEXT_TIMEOUT = 5


def _notification_cache_key(user_id):
    return f"dashboard-notifications:{user_id}"


def dashboard_notifications(request):
    if not request.user.is_authenticated:
        return {"dashboard_notifications": [], "unread_notification_count": 0}
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
    }
