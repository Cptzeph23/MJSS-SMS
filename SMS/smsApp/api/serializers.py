# Absolute path: SMS/smsApp/api/serializers.py
from rest_framework import serializers

from smsApp.models import (
    AttendanceRecord,
    AttendanceSession,
    Class,
    ClassSubject,
    Notification,
    Stream,
    Student,
    Subject,
    User,
)


class UserMeSerializer(serializers.ModelSerializer):
    """Spec §26 'Authentication', 'Dashboard data' — the Flutter app's
    first call after login to learn who's signed in and where to route
    them, mirroring smsApp.services.get_dashboard_url_for_role() but as
    data instead of an HTTP redirect (a mobile app can't follow a
    Django URL redirect the way a browser does)."""

    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ["id", "username", "full_name", "email", "role", "is_locked"]

    def get_full_name(self, obj) -> str:
        return obj.get_full_name() or obj.username


class ClassMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Class
        fields = ["id", "name"]


class StreamMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stream
        fields = ["id", "name"]


class StudentSerializer(serializers.ModelSerializer):
    """Spec §26 'Students'. Deliberately excludes financial fields
    entirely — spec §23's academic/financial separation applies to API
    responses exactly as it does to the Django dashboard querysets."""

    full_name = serializers.SerializerMethodField()
    current_class = ClassMiniSerializer(read_only=True)
    current_stream = StreamMiniSerializer(read_only=True)

    class Meta:
        model = Student
        fields = [
            "id", "admission_number", "full_name", "status",
            "current_class", "current_stream", "photo",
        ]

    def get_full_name(self, obj) -> str:
        return obj.user.get_full_name() or obj.user.username


class SubjectMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subject
        fields = ["id", "name", "code"]


class ClassSubjectSerializer(serializers.ModelSerializer):
    subject = SubjectMiniSerializer(read_only=True)
    class_group = ClassMiniSerializer(read_only=True)

    class Meta:
        model = ClassSubject
        fields = ["id", "subject", "class_group"]


class AttendanceRecordSerializer(serializers.ModelSerializer):
    """Spec §26 'Attendance'. Read-only in this API slice — marking
    attendance is a teacher action with conflict/ownership checks
    (smsApp.services.mark_attendance()) that belongs in a dedicated
    write endpoint, deferred alongside the other write-heavy resource
    groups (see Phase 22 summary)."""

    student_name = serializers.SerializerMethodField()
    class_subject = serializers.SerializerMethodField()
    date = serializers.DateField(source="session.date", read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = ["id", "student_name", "class_subject", "date", "status", "notes"]

    def get_student_name(self, obj) -> str:
        return obj.student.user.get_full_name() or obj.student.user.username

    def get_class_subject(self, obj) -> str:
        cs = obj.session.class_subject
        return f"{cs.class_group.name} - {cs.subject.name}"


class NotificationSerializer(serializers.ModelSerializer):
    """Spec §26 'Notifications'. Read + mark-read (via the ViewSet
    action) — this is the one write path in this resource slice, and
    it's inherently safe (a user can only ever mark their own
    notifications, enforced by the queryset in the ViewSet)."""

    class Meta:
        model = Notification
        fields = ["id", "notification_type", "title", "message", "is_read", "created_at", "read_at"]
        read_only_fields = ["id", "notification_type", "title", "message", "created_at", "read_at"]