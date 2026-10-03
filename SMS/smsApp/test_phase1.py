import csv
import tempfile
from datetime import date
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import Class, Program, School, Staff, Student, User


class Phase1RoleAndIsolationTests(TestCase):
    def setUp(self):
        self.school_a = School.objects.create(name="A School", code="A")
        self.school_b = School.objects.create(name="B School", code="B")
        self.admin = User.objects.create_user(
            username="principal", password="pass12345",
            role=User.Role.PRINCIPAL_DIRECTOR,
        )
        Staff.objects.create(
            user=self.admin, school=self.school_a, staff_id="P-A",
            job_title="Principal", date_hired=date(2026, 1, 1),
        )

    def test_principal_role_routes_to_school_dashboard(self):
        self.client.login(username="principal", password="pass12345")
        response = self.client.get("/")
        self.assertRedirects(response, "/academic-admin/")

    def test_principal_cannot_view_another_school_student(self):
        foreign_user = User.objects.create_user(
            username="foreignstudent", password="pass12345", role=User.Role.STUDENT
        )
        foreign_student = Student.objects.create(
            user=foreign_user, school=self.school_b, admission_number="B-001",
            admission_date=date(2026, 1, 1),
        )
        self.client.login(username="principal", password="pass12345")
        response = self.client.get(f"/academic-admin/students/{foreign_student.pk}/")
        self.assertEqual(response.status_code, 404)

    def test_deputy_cannot_register_student(self):
        deputy = User.objects.create_user(
            username="deputy", password="pass12345", role=User.Role.DEPUTY_PRINCIPAL
        )
        Staff.objects.create(
            user=deputy, school=self.school_a, staff_id="D-A",
            job_title="Deputy Principal", date_hired=date(2026, 1, 1),
        )
        self.client.login(username="deputy", password="pass12345")
        response = self.client.post("/academic-admin/students/", {})
        self.assertEqual(response.status_code, 403)


class StudentImportTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Import School", code="IMP")
        program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=program, name="Grade 5"
        )

    def test_import_creates_and_updates_without_recreating_student(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", newline="", delete=False) as source:
            writer = csv.DictWriter(
                source, fieldnames=["username", "first_name", "last_name", "email",
                                    "admission_number", "admission_date", "gender", "class_name"]
            )
            writer.writeheader()
            writer.writerow({
                "username": "imported", "first_name": "Ada", "last_name": "One",
                "email": "ada@example.com", "admission_number": "IMP-001",
                "admission_date": "2026-01-10", "gender": "F", "class_name": "Grade 5",
            })
            path = Path(source.name)
        try:
            call_command("import_students", str(path), school="IMP")
            student = Student.objects.get(admission_number="IMP-001")
            student_id = student.pk
            call_command("import_students", str(path), school="IMP")
            student.refresh_from_db()
            self.assertEqual(student.pk, student_id)
            self.assertEqual(student.user.first_name, "Ada")
            self.assertEqual(student.current_class_id, self.class_group.pk)
        finally:
            path.unlink(missing_ok=True)

    def test_import_rejects_missing_required_column(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as source:
            source.write("username,first_name\nstudent,Ada\n")
            path = Path(source.name)
        try:
            with self.assertRaises(CommandError):
                call_command("import_students", str(path), school="IMP")
        finally:
            path.unlink(missing_ok=True)
