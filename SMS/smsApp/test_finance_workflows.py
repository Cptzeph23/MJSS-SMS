from datetime import date
from decimal import Decimal
from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from .models import (
    AcademicYear, Class, FeeCategory, Guardian, Program, School, Staff,
    Student, StudentGuardian, Term, User,
)


class FinanceWorkflowTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Finance School", code="FIN")
        self.year = AcademicYear.objects.create(
            school=self.school, name="2026", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31), is_current=True,
        )
        self.term = Term.objects.create(
            academic_year=self.year, name="Term 1", term_number=1,
            start_date=date(2026, 1, 1), end_date=date(2026, 4, 30),
            is_current=True,
        )
        self.program = Program.objects.create(
            school=self.school, name="Primary", code="PRI",
        )
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 1",
        )
        self.category = FeeCategory.objects.create(
            school=self.school, name="Tuition", code="TUI",
        )
        self.finance_user = User.objects.create_user(
            username="financeworkflow", password="pass12345",
            role=User.Role.FINANCE_ADMIN,
        )
        Staff.objects.create(
            user=self.finance_user, school=self.school, staff_id="FIN-1",
            job_title="Finance Officer", date_hired=date(2025, 1, 1),
        )

    def test_class_fee_structure_creates_one_invoice_per_active_student(self):
        for number in range(1, 3):
            user = User.objects.create_user(
                username=f"grade1student{number}", password="pass12345",
                role=User.Role.STUDENT, first_name=f"Student {number}",
            )
            Student.objects.create(
                user=user, school=self.school, current_class=self.class_group,
                admission_number=f"G1-{number}", admission_date=date(2026, 1, 10),
            )

        self.client.login(username="financeworkflow", password="pass12345")
        response = self.client.post(
            reverse("dashboard:finance_fee_structures"),
            {
                "name": "Grade 1 Term 1 Fees",
                "academic_year_id": self.year.pk,
                "term_id": self.term.pk,
                "class_id": self.class_group.pk,
                "category_id": self.category.pk,
                "amount": "15000",
                "due_date": "2026-04-01",
            },
        )
        self.assertRedirects(response, reverse("dashboard:finance_fee_structures"))
        structure = self.school.fee_structures.get(name="Grade 1 Term 1 Fees")
        self.assertEqual(structure.class_group_id, self.class_group.pk)
        self.assertEqual(structure.invoices.count(), 2)
        self.assertEqual(
            structure.invoices.first().total_amount, Decimal("15000")
        )

    def test_family_payment_page_is_utf8_and_lists_registered_family(self):
        guardian = Guardian.objects.create(
            school=self.school, first_name="Jose", last_name="Nunez",
            relationship="Parent", phone_number="+254700000001",
        )
        user = User.objects.create_user(
            username="familychild", password="pass12345", role=User.Role.STUDENT,
        )
        student = Student.objects.create(
            user=user, school=self.school, current_class=self.class_group,
            admission_number="FAM-1", admission_date=date(2026, 1, 10),
        )
        StudentGuardian.objects.create(student=student, guardian=guardian)

        self.client.login(username="financeworkflow", password="pass12345")
        response = self.client.get(reverse("dashboard:finance_family_payment"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Jose")
        self.assertContains(response, "Nunez")
        self.assertNotContains(response, "UnicodeDecodeError")
        Path("templates/dashboard/finance/family_payment.html").read_text(encoding="utf-8")
