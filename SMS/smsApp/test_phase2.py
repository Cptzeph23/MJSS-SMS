from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AcademicYear, FeeCategory, FeeStructure, Guardian, Invoice, Payment,
    School, Student, StudentGuardian, User,
)
from .services import compute_family_account_summary, record_family_payment


class FamilyFinanceTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Family School", code="FAM")
        self.year = AcademicYear.objects.create(
            school=self.school, name="2026", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.category = FeeCategory.objects.create(
            school=self.school, name="Tuition", code="TUI"
        )
        self.structure = FeeStructure.objects.create(
            school=self.school, academic_year=self.year, name="Standard"
        )
        self.parent_user = User.objects.create_user(
            username="familyparent", password="pass12345", role=User.Role.PARENT
        )
        self.guardian = Guardian.objects.create(
            user=self.parent_user, school=self.school, first_name="Family",
            last_name="Parent", relationship="Parent", phone_number="+254700000001",
        )
        self.students = []
        self.invoices = []
        for index, amount in enumerate((30000, 25000, 35000), start=1):
            user = User.objects.create_user(
                username=f"familychild{index}", password="pass12345",
                role=User.Role.STUDENT, first_name=f"Child {index}",
            )
            student = Student.objects.create(
                user=user, school=self.school, admission_number=f"FAM-{index}",
                admission_date=date(2026, 1, 10),
            )
            StudentGuardian.objects.create(
                student=student, guardian=self.guardian, is_billing_contact=True
            )
            invoice = Invoice.objects.create(
                student=student, school=self.school, academic_year=self.year,
                fee_structure=self.structure, total_amount=Decimal(amount),
                issue_date=date(2026, 1, 1), due_date=date(2026, 4, 1),
            )
            self.students.append(student)
            self.invoices.append(invoice)
        self.finance_user = User.objects.create_user(
            username="familyfinance", password="pass12345",
            role=User.Role.FINANCE_ADMIN,
        )

    def test_family_payment_totals_and_child_allocations(self):
        payment = record_family_payment(
            guardian=self.guardian, amount=Decimal("50000"),
            allocations=[
                (self.invoices[0], Decimal("30000")),
                (self.invoices[1], Decimal("20000")),
            ],
            payment_method=Payment.Method.CASH, payment_date=timezone.now(),
            received_by=self.finance_user, payer_name="Family Parent",
        )
        summary = compute_family_account_summary(guardian=self.guardian)
        self.assertEqual(summary["total_billed"], Decimal("90000"))
        self.assertEqual(summary["total_paid"], Decimal("50000"))
        # Positive is a family credit; a balance still owed is negative.
        self.assertEqual(summary["outstanding_balance"], Decimal("-40000"))
        self.assertEqual(payment.allocations.count(), 2)
        balances = {row["student"].admission_number: row["outstanding_balance"] for row in summary["children"]}
        self.assertEqual(balances["FAM-1"], Decimal("0"))
        self.assertEqual(balances["FAM-2"], Decimal("-5000"))
        self.assertEqual(balances["FAM-3"], Decimal("-35000"))
        self.assertTrue(hasattr(payment, "receipt"))

    def test_family_payment_rejects_unrelated_invoice(self):
        other_user = User.objects.create_user(
            username="otherchild", password="pass12345", role=User.Role.STUDENT
        )
        other = Student.objects.create(
            user=other_user, school=self.school, admission_number="OTHER-1",
            admission_date=date(2026, 1, 10),
        )
        other_invoice = Invoice.objects.create(
            student=other, school=self.school, academic_year=self.year,
            fee_structure=self.structure, total_amount=Decimal("1000"),
            issue_date=date(2026, 1, 1), due_date=date(2026, 4, 1),
        )
        with self.assertRaises(ValueError):
            record_family_payment(
                guardian=self.guardian, amount=Decimal("1000"),
                allocations=[(other_invoice, Decimal("1000"))],
                payment_method=Payment.Method.CASH, payment_date=timezone.now(),
                received_by=self.finance_user,
            )

    def test_parent_finance_page_shows_family_totals(self):
        self.client.login(username="familyparent", password="pass12345")
        response = self.client.get(
            reverse("dashboard:parent_child_finance", args=[self.students[0].pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Family Account")
        self.assertContains(response, "90000")
        self.assertContains(response, "FAM-2")
