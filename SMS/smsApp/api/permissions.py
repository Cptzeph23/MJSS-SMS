# Absolute path: SMS/smsApp/api/permissions.py
"""
DRF permission classes.

Mirrors the same role-based access rules already enforced by
smsApp.permissions.RoleRequiredMixin for the Django template views —
spec §3 'API-first architecture' means the same authorization rules
apply regardless of which client (browser or Flutter app) is asking.
"""
from rest_framework.permissions import BasePermission

from smsApp.models import User


class RoleIn(BasePermission):
    """Base class for a permission that allows a fixed set of roles (plus
    superusers, always). Subclass and set `allowed_roles`."""

    allowed_roles: list[str] = []

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        if getattr(request.user, "is_locked", False) or not request.user.is_active:
            return False
        return request.user.role in self.allowed_roles


class IsStudent(RoleIn):
    allowed_roles = [User.Role.STUDENT]


class IsParent(RoleIn):
    allowed_roles = [User.Role.PARENT]


class IsTeacher(RoleIn):
    allowed_roles = [User.Role.TEACHER, User.Role.CLASS_TEACHER]


class IsAcademicStaff(RoleIn):
    """Any role permitted to view/manage academic data (not finance) —
    used by endpoints like attendance and students that Academic Admin,
    teachers, and class teachers all need read access to."""

    allowed_roles = [
        User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN, User.Role.TEACHER,
        User.Role.CLASS_TEACHER, User.Role.EXAM_OFFICER,
    ]