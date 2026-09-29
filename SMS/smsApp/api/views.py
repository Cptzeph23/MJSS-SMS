# Absolute path: SMS/smsApp/api/views.py
"""
Spec §25/§26: REST API layer, API-first architecture.

Every view here reads from the same querysets/service functions the
Django template dashboards already use (Phases 16-21) — this file adds
a JSON representation on top of already-tested business logic, it does
not reimplement authorization or data-scoping rules from scratch.
"""
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView
from drf_spectacular.utils import extend_schema

from smsApp.models import AttendanceRecord, ClassSubject, Notification, Student
from smsApp.services import get_children_for_guardian, mark_notification_read

from .permissions import IsAcademicStaff
from .serializers import (
    AttendanceRecordSerializer,
    ClassSubjectSerializer,
    NotificationSerializer,
    StudentSerializer,
    UserMeSerializer,
)


class SchoolTokenObtainPairView(TokenObtainPairView):
    """Spec §26 'Authentication' — same login credentials as the web app,
    issuing a JWT pair instead of a session cookie. Django's session-based
    LoginView (Phase 4) remains the auth path for the browser dashboards;
    this is the mobile-client equivalent, not a replacement."""


class MeView(APIView):
    """Spec §26 'Dashboard data' entry point — tells the Flutter app who
    is signed in and their role, so the app can route to the correct
    role-specific screen flow (mirrors
    smsApp.services.get_dashboard_url_for_role(), but as JSON a mobile
    client can act on directly)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=UserMeSerializer)
    def get(self, request):
        return Response(UserMeSerializer(request.user).data)


class MyClassSubjectsView(APIView):
    """Spec §26 'Courses'. Student -> their enrolled subjects; Teacher ->
    their assigned subjects. Reuses the exact querysets the Django
    dashboards use (Phase 16's Student LMS view, Phase 18's Teacher
    classes view) rather than redefining the scoping logic."""

    permission_classes = [IsAuthenticated]

    @extend_schema(responses=ClassSubjectSerializer(many=True))
    def get(self, request):
        user = request.user
        if user.role == user.Role.STUDENT:
            student = Student.objects.filter(user=user).first()
            if student is None:
                return Response([], status=status.HTTP_200_OK)
            class_subjects = ClassSubject.objects.filter(
                enrollments__student=student
            ).distinct().select_related("subject", "class_group")
        elif user.role in (user.Role.TEACHER, user.Role.CLASS_TEACHER):
            from smsApp.models import Staff
            staff = Staff.objects.filter(user=user).first()
            if staff is None:
                return Response([], status=status.HTTP_200_OK)
            class_subjects = ClassSubject.objects.filter(
                teaching_assignments__teacher=staff, teaching_assignments__is_active=True,
            ).distinct().select_related("subject", "class_group")
        else:
            return Response(
                {"detail": "This endpoint is only available to students and teachers."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response(ClassSubjectSerializer(class_subjects, many=True).data)


class StudentViewSet(mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Spec §26 'Students'. Read-only in this API slice; scoped exactly
    like the Django dashboards (spec §23 financial/academic separation
    — no financial fields ever appear on StudentSerializer):
    - Academic staff: every student at their school.
    - A student: only their own record.
    - A parent: only their linked children (Phase 17's
      get_children_for_guardian(), reused verbatim).
    Any other role gets an empty queryset rather than a 403, so a
    mis-scoped client sees 'no data' instead of learning role details
    through error messages."""

    serializer_class = StudentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        base = Student.objects.select_related("user", "current_class", "current_stream")
        if user.is_superuser or IsAcademicStaff().has_permission(self.request, self):
            return base.all()
        if user.role == user.Role.STUDENT:
            return base.filter(user=user)
        if user.role == user.Role.PARENT:
            return base.filter(pk__in=get_children_for_guardian(guardian_user=user).values("pk"))
        return base.none()


class AttendanceViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """Spec §26 'Attendance'. Same scoping pattern as StudentViewSet —
    a student sees only their own attendance, a parent only their
    children's, academic staff see everyone at the school."""

    serializer_class = AttendanceRecordSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        base = AttendanceRecord.objects.select_related(
            "student__user", "session__class_subject__subject", "session__class_subject__class_group",
        )
        if user.is_superuser or IsAcademicStaff().has_permission(self.request, self):
            return base.all()
        if user.role == user.Role.STUDENT:
            return base.filter(student__user=user)
        if user.role == user.Role.PARENT:
            children = get_children_for_guardian(guardian_user=user)
            return base.filter(student__in=children)
        return base.none()


class NotificationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """Spec §26 'Notifications'. Every user (any role) sees only their
    own notifications — the queryset itself is the security boundary,
    identical in spirit to smsApp.views.StudentMarkNotificationReadView
    etc. from the Django dashboards."""

    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).order_by("-created_at")

    @action(detail=True, methods=["post"])
    def mark_read(self, request, pk=None):
        notification = self.get_object()
        mark_notification_read(notification=notification)
        return Response(NotificationSerializer(notification).data)