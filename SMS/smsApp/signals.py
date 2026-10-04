"""Signals for invalidating cached school hostname mappings."""

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .models import School


@receiver(pre_save, sender=School)
def remember_previous_school_subdomain(sender, instance, **kwargs):
    if not instance.pk:
        instance._previous_tenant_subdomain = None
        return
    instance._previous_tenant_subdomain = (
        sender.objects.filter(pk=instance.pk)
        .values_list("subdomain", flat=True)
        .first()
    )


def _invalidate_tenant_mappings(*subdomains):
    keys = [
        f"tenant:subdomain:{subdomain.lower()}"
        for subdomain in set(subdomains)
        if subdomain
    ]
    if keys:
        cache.delete_many(keys)


@receiver(post_save, sender=School)
def invalidate_saved_school_subdomains(sender, instance, **kwargs):
    _invalidate_tenant_mappings(
        getattr(instance, "_previous_tenant_subdomain", None),
        instance.subdomain,
    )


@receiver(post_delete, sender=School)
def invalidate_deleted_school_subdomain(sender, instance, **kwargs):
    _invalidate_tenant_mappings(instance.subdomain)
