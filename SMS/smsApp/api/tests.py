# Absolute path: SMS/smsApp/api/tests.py
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from smsApp.models import (
    AcademicYear,
    AttendanceRecord,
    AttendanceSession,
    Class,
    ClassSubject,
    Enrollment,
    Guardian,
    Notification,
    Program,
    School,
    Staff,
    Student,
    StudentGuardian,
    Subject,
    TeachingAssignment,
    Term,
)

User = get_user_model()


class JWTAuthTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="apiauthuser", password="pass12345", role=User.Role.STUDENT
        )

    def test_obtain_token_with_valid_credentials(self):
        response = self.client.post(
            "/api/v1/auth/token/",
            {"username": "apiauthuser", "password": "pass12345"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)

    def test_obtain_token_with_wrong_password_rejected(self):
        response = self.client.post(
            "/api/v1/auth/token/",
            {"username": "apiauthuser", "password": "wrongpassword"},
            format="json",
        )
        self.assertEqual(response.status_code, 401)

    def test_refresh_token_issues_new_access_token(self):
        obtain = self.client.post(
            "/api/v1/auth/token/",
            {"username": "apiauthuser", "password": "pass12345"},
            format="json",
        )
        response = self.client.post(
            "/api/v1/auth/token/refresh/",
            {"refresh": obtain.data["refresh"]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_rotated_refresh_token_is_blacklisted(self):
        """Regression test: SIMPLE_JWT['BLACKLIST_AFTER_ROTATION'] silently
        does nothing unless 'rest_framework_simplejwt.token_blacklist' is
        in INSTALLED_APPS — this confirms the app is actually installed
        and blacklisting genuinely happens on rotation, not just that the
        setting is present."""
        obtain = self.client.post(
            "/api/v1/auth/token/",
            {"username": "apiauthuser", "password": "pass12345"},
            format="json",
        )
        old_refresh = obtain.data["refresh"]

        # First use rotates it (issues a new refresh token, blacklists the old one).
        self.client.post("/api/v1/auth/token/refresh/", {"refresh": old_refresh}, format="json")

        # Reusing the now-blacklisted refresh token must be rejected.
        reuse_response = self.client.post(
            "/api/v1/auth/token/refresh/", {"refresh": old_refresh}, format="json"
        )
        self.assertEqual(reuse_response.status_code, 401)

    def test_unauthenticated_request_to_me_endpoint_returns_401(self):
        response = self.client.get("/api/v1/users/me/")
        self.assertEqual(response.status_code, 401)


class MeEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="apimeuser", password="pass12345", role=User.Role.TEACHER,
            first_name="Sam", last_name="Otieno",
        )
        self.client.force_authenticate(user=self.user)

    def test_me_endpoint_returns_correct_user_data(self):
        response = self.client.get("/api/v1/users/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], "apimeuser")
        self.assertEqual(response.data["role"], "TEACHER")
        self.assertEqual(response.data["full_name"], "Sam Otieno")


class StudentAPIScopingTests(TestCase):
    """The highest-stakes test class in this file — confirms the API
    enforces the exact same data-isolation guarantees the Django
    dashboards already enforce (Phases 16/17/21): a student sees only
    themselves, a parent sees only their own children, academic staff
    see everyone at the school, and no financial data ever appears."""

    def setUp(self):
        self.client = APIClient()
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )

        student1_user = User.objects.create_user(
            username="apistudent1", password="pass12345", role=User.Role.STUDENT
        )
        self.student1 = Student.objects.create(
            user=student1_user, school=self.school, admission_number="ADM001",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        student2_user = User.objects.create_user(
            username="apistudent2", password="pass12345", role=User.Role.STUDENT
        )
        self.student2 = Student.objects.create(
            user=student2_user, school=self.school, admission_number="ADM002",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )

        parent_user = User.objects.create_user(
            username="apiparent1", password="pass12345", role=User.Role.PARENT
        )
        self.guardian = Guardian.objects.create(
            user=parent_user, school=self.school, first_name="Jane", last_name="Doe",
            relationship="Mother", phone_number="+254700000000",
        )
        StudentGuardian.objects.create(student=self.student1, guardian=self.guardian)
        # Deliberately NOT linking student2 to this guardian.

        self.academic_admin_user = User.objects.create_user(
            username="apiacademicadmin", password="pass12345", role=User.Role.ACADEMIC_ADMIN
        )

        self.student1_user = student1_user
        self.parent_user = parent_user

    def test_student_sees_only_own_record(self):
        self.client.force_authenticate(user=self.student1_user)
        response = self.client.get("/api/v1/students/")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["admission_number"], "ADM001")

    def test_student_cannot_retrieve_another_students_record_by_id(self):
        self.client.force_authenticate(user=self.student1_user)
        response = self.client.get(f"/api/v1/students/{self.student2.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_parent_sees_only_linked_children(self):
        self.client.force_authenticate(user=self.parent_user)
        response = self.client.get("/api/v1/students/")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["admission_number"], "ADM001")

    def test_parent_cannot_retrieve_unrelated_student(self):
        self.client.force_authenticate(user=self.parent_user)
        response = self.client.get(f"/api/v1/students/{self.student2.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_academic_admin_sees_all_students_at_school(self):
        self.client.force_authenticate(user=self.academic_admin_user)
        response = self.client.get("/api/v1/students/")
        self.assertEqual(response.data["count"], 2)

    def test_student_serializer_never_includes_financial_fields(self):
        """Spec §23: financial/academic separation applies to the API
        exactly as it does to the Django dashboards."""
        self.client.force_authenticate(user=self.academic_admin_user)
        response = self.client.get("/api/v1/students/")
        content = str(response.data)
        for forbidden_term in ["invoice", "payment", "balance", "fee"]:
            self.assertNotIn(forbidden_term, content.lower())

    def test_unrelated_role_gets_empty_list_not_error(self):
        finance_user = User.objects.create_user(
            username="apifinanceperson", password="pass12345", role=User.Role.FINANCE_ADMIN
        )
        self.client.force_authenticate(user=finance_user)
        response = self.client.get("/api/v1/students/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)


class AttendanceAPIScopingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )
        self.subject = Subject.objects.create(school=self.school, code="MATH", name="Mathematics")
        self.class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        student1_user = User.objects.create_user(
            username="apiattstudent1", password="pass12345", role=User.Role.STUDENT
        )
        self.student1 = Student.objects.create(
            user=student1_user, school=self.school, admission_number="ADM101",
            admission_date=datetime.date(2026, 1, 10),
        )
        student2_user = User.objects.create_user(
            username="apiattstudent2", password="pass12345", role=User.Role.STUDENT
        )
        self.student2 = Student.objects.create(
            user=student2_user, school=self.school, admission_number="ADM102",
            admission_date=datetime.date(2026, 1, 10),
        )
        session = AttendanceSession.objects.create(
            class_subject=self.class_subject, term=self.term, date=datetime.date(2026, 2, 2),
        )
        AttendanceRecord.objects.create(session=session, student=self.student1, status="PRESENT")
        AttendanceRecord.objects.create(session=session, student=self.student2, status="ABSENT")
        self.student1_user = student1_user

    def test_student_sees_only_own_attendance(self):
        self.client.force_authenticate(user=self.student1_user)
        response = self.client.get("/api/v1/attendance/")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["status"], "PRESENT")


class NotificationAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user1 = User.objects.create_user(
            username="apinotifuser1", password="pass12345", role=User.Role.STUDENT
        )
        self.user2 = User.objects.create_user(
            username="apinotifuser2", password="pass12345", role=User.Role.STUDENT
        )
        self.notification = Notification.objects.create(
            recipient=self.user1, notification_type="OTHER", title="Test", message="Hello",
        )
        Notification.objects.create(
            recipient=self.user2, notification_type="OTHER", title="Not yours", message="Hi",
        )

    def test_user_sees_only_own_notifications(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.get("/api/v1/notifications/")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["title"], "Test")

    def test_mark_read_action_updates_own_notification(self):
        self.client.force_authenticate(user=self.user1)
        response = self.client.post(f"/api/v1/notifications/{self.notification.pk}/mark_read/")
        self.assertEqual(response.status_code, 200)
        self.notification.refresh_from_db()
        self.assertTrue(self.notification.is_read)

    def test_cannot_mark_another_users_notification_read(self):
        other_notification = Notification.objects.get(recipient=self.user2)
        self.client.force_authenticate(user=self.user1)
        response = self.client.post(f"/api/v1/notifications/{other_notification.pk}/mark_read/")
        self.assertEqual(response.status_code, 404)


class MyClassSubjectsAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )
        self.subject = Subject.objects.create(school=self.school, code="MATH", name="Mathematics")
        self.class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        student_user = User.objects.create_user(
            username="apicoursestudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM200",
            admission_date=datetime.date(2026, 1, 10),
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject,
            academic_year=self.academic_year,
        )
        self.student_user = student_user

        teacher_user = User.objects.create_user(
            username="apicourseteacher", password="pass12345", role=User.Role.TEACHER
        )
        self.staff = Staff.objects.create(
            user=teacher_user, school=self.school, staff_id="STF500",
            job_title="Maths Teacher", date_hired=datetime.date(2024, 1, 1),
        )
        TeachingAssignment.objects.create(
            class_subject=self.class_subject, teacher=self.staff, term=self.term
        )
        self.teacher_user = teacher_user

    def test_student_sees_enrolled_subjects(self):
        self.client.force_authenticate(user=self.student_user)
        response = self.client.get("/api/v1/courses/mine/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["subject"]["name"], "Mathematics")

    def test_teacher_sees_assigned_subjects(self):
        self.client.force_authenticate(user=self.teacher_user)
        response = self.client.get("/api/v1/courses/mine/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_other_role_gets_403(self):
        parent_user = User.objects.create_user(
            username="apicourseparent", password="pass12345", role=User.Role.PARENT
        )
        self.client.force_authenticate(user=parent_user)
        response = self.client.get("/api/v1/courses/mine/")
        self.assertEqual(response.status_code, 403)


class OpenAPISchemaTests(TestCase):
    """Spec §25: 'Document the APIs using OpenAPI/Swagger-compatible
    documentation.' Confirms the schema actually generates without
    errors, not just that the URL is wired."""

    def test_schema_endpoint_returns_valid_openapi_document(self):
        client = APIClient()
        user = User.objects.create_user(
            username="apischemauser", password="pass12345", role=User.Role.SUPER_ADMIN,
            is_superuser=True,
        )
        client.force_authenticate(user=user)
        response = client.get("/api/schema/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"openapi", response.content[:50])

    def test_swagger_ui_loads(self):
        client = APIClient()
        response = client.get("/api/docs/")
        self.assertEqual(response.status_code, 200)