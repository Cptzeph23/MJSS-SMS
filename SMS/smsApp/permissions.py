# Absolute path: SMS/smsApp/permissions.py
"""
Access control helpers.

Per the RBAC design note in Phase 1 (smsApp/models.py, User.role docstring):
`role` drives UI/dashboard routing, but authorization decisions must check
Django permissions/groups. `RoleRequiredMixin` below checks `role` for
convenience (fast dashboard routing) AND still respects `is_active`/
`is_locked`; fine-grained per-object permission checks (e.g. 'can approve
this student's result') are added as `PermissionRequiredMixin` checks on
top of this in later phases, once those permissions exist (Phase 3+ models
declare them via `Meta.permissions`).

Server-side enforcement only — never trust a hidden form field or JS check
(spec §3 'Security first', §36 'Never rely on JavaScript validation alone').
"""
from __future__ import annotations

from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


class RoleRequiredMixin(AccessMixin):
    """Class-based view mixin. Set `allowed_roles = [User.Role.SUPER_ADMIN]`
    on the view. Locked/inactive accounts are always denied regardless of
    role (spec §5 'Lock account' must actually block access)."""

    allowed_roles: list[str] = []

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        if getattr(request.user, "is_locked", False) or not request.user.is_active:
            return redirect("dashboard:account_locked")

        if request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        if self.allowed_roles and request.user.role not in self.allowed_roles:
            raise PermissionDenied("You do not have access to this page.")

        return super().dispatch(request, *args, **kwargs)


def role_required(*roles: str):
    """Function-based view decorator equivalent of RoleRequiredMixin."""

    def decorator(view_func):
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect("dashboard:login")
            if getattr(request.user, "is_locked", False) or not request.user.is_active:
                return redirect("dashboard:account_locked")
            if request.user.is_superuser or request.user.role in roles:
                return view_func(request, *args, **kwargs)
            raise PermissionDenied("You do not have access to this page.")

        return wrapped

    return decorator