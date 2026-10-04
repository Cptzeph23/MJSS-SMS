
from django.apps import AppConfig


class SmsappConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "smsApp"
    verbose_name = "Accounts & RBAC Core"

    def ready(self):
        # Register tenant-cache invalidation hooks only after the app registry
        # is ready. Changes to school status/subdomains must take effect now,
        # not after the five-minute tenant cache TTL.
        from . import signals  # noqa: F401
