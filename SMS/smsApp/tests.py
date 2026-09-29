# Absolute path: SMS/smsApp/tests.py
import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import (
    AcademicYear,
    Announcement,
    Assessment,
    AssessmentComponent,
    AssessmentMark,
    AssessmentStructure,
    AssessmentType,
    Assignment,
    AssignmentSubmission,
    AttendanceRecord,
    AttendanceSession,
    AuditLog,
    Book,
    BookCategory,
    BookCopy,
    Borrowing,
    Class,
    ClassSubject,
    CourseMaterial,
    Department,
    Discussion,
    DiscussionReply,
    Enrollment,
    FeeCategory,
    FeeConcession,
    FeeStructure,
    FeeStructureItem,
    FinancialAdjustment,
    GradeBand,
    GradingScheme,
    Guardian,
    Invoice,
    InvoiceLineItem,
    LeaveRequest,
    LibrarySettings,
    LoginHistory,
    Notification,
    NotificationDelivery,
    NotificationPreference,
    Payment,
    Program,
    Quiz,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    Receipt,
    Refund,
    ReportCard,
    ReportTemplate,
    ResultAmendmentRequest,
    Room,
    Period,
    School,
    Staff,
    StaffAttendanceRecord,
    Student,
    StudentGuardian,
    Subject,
    TeachingAssignment,
    Term,
    TimetableSlot,
    Transcript,
)
from .services import (
    apply_financial_adjustment,
    borrow_book,
    change_student_status,
    compute_school_academic_summary,
    correct_attendance_record,
    create_announcement,
    create_timetable_slot,
    compute_weighted_average,
    compute_student_account_summary,
    compute_school_financial_summary,
    compute_staff_workload,
    deactivate_staff,
    decide_leave_request,
    decide_refund,
    decide_result_amendment,
    generate_batch_reports,
    generate_invoice_for_student,
    generate_report_pdf,
    generate_transcript,
    get_grade_for_mark,
    grade_assignment_submission,
    grade_quiz_short_answer,
    mark_book_lost,
    mark_notification_read,
    notify_assignment_deadline_approaching,
    notify_payment_received,
    notify_report_available,
    notify_result_published,
    reactivate_staff,
    register_student,
    record_staff_attendance,
    pay_library_fine,
    record_payment,
    reject_assessment,
    reschedule_timetable_slot,
    request_refund,
    request_result_amendment,
    render_report_html,
    return_book,
    send_notification,
    submit_assignment,
    submit_quiz_attempt,
    submit_leave_request,
    transition_assessment_workflow,
    validate_grade_bands_no_overlap,
    verify_transcript,
)
from .validators import (
    validate_document_content,
    validate_file_size,
    validate_image_content,
    validate_pdf_content,
)

User = get_user_model()


class UserModelTests(TestCase):
    def test_user_created_with_default_student_role(self):
        user = User.objects.create_user(username="jdoe", password="pass12345")
        self.assertEqual(user.role, User.Role.STUDENT)

    def test_user_str_includes_role_display(self):
        user = User.objects.create_user(
            username="ateacher", password="pass12345", role=User.Role.TEACHER
        )
        self.assertIn("Teacher", str(user))


class AcademicStructureTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(
            school=self.school, name="8-4-4", code="844"
        )

    def test_school_code_must_be_unique(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                School.objects.create(name="Other School", code="RVH")

    def test_only_one_current_academic_year_per_school(self):
        ay1 = AcademicYear.objects.create(
            school=self.school, name="2025/2026",
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 12, 31),
            is_current=True,
        )
        ay2 = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
            is_current=True,
        )
        ay1.refresh_from_db()
        self.assertFalse(ay1.is_current)
        self.assertTrue(ay2.is_current)

    def test_academic_year_end_before_start_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicYear.objects.create(
                    school=self.school, name="Bad Year",
                    start_date=datetime.date(2026, 12, 31),
                    end_date=datetime.date(2026, 1, 1),
                )

    def test_class_name_unique_per_program_not_globally(self):
        program2 = Program.objects.create(school=self.school, name="CBC", code="CBC")
        Class.objects.create(school=self.school, program=self.program, name="Grade 10")
        # Same class name allowed under a different program — should not raise.
        Class.objects.create(school=self.school, program=program2, name="Grade 10")

    def test_term_requires_unique_term_number_per_year(self):
        ay = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
        )
        Term.objects.create(
            academic_year=ay, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Term.objects.create(
                    academic_year=ay, name="Term 1 Duplicate", term_number=1,
                    start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2026, 8, 1),
                )


class PeopleModelTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )

    def _make_student_user(self, username="student1"):
        return User.objects.create_user(
            username=username, password="pass12345", role=User.Role.STUDENT
        )

    def test_admission_number_unique_per_school_not_globally(self):
        school2 = School.objects.create(name="Lakeside Academy", code="LKA")
        Student.objects.create(
            user=self._make_student_user("s1"),
            school=self.school, admission_number="ADM001",
            admission_date=datetime.date(2026, 1, 10),
        )
        # Same admission number at a different school must be allowed.
        Student.objects.create(
            user=self._make_student_user("s2"),
            school=school2, admission_number="ADM001",
            admission_date=datetime.date(2026, 1, 10),
        )

    def test_duplicate_admission_number_same_school_rejected(self):
        Student.objects.create(
            user=self._make_student_user("s3"),
            school=self.school, admission_number="ADM002",
            admission_date=datetime.date(2026, 1, 10),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Student.objects.create(
                    user=self._make_student_user("s4"),
                    school=self.school, admission_number="ADM002",
                    admission_date=datetime.date(2026, 1, 10),
                )

    def test_student_default_status_is_active(self):
        student = Student.objects.create(
            user=self._make_student_user("s5"),
            school=self.school, admission_number="ADM003",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.assertEqual(student.status, Student.Status.ACTIVE)

    def test_guardian_linked_via_through_model_with_primary_contact_flag(self):
        student = Student.objects.create(
            user=self._make_student_user("s6"),
            school=self.school, admission_number="ADM004",
            admission_date=datetime.date(2026, 1, 10),
        )
        guardian = Guardian.objects.create(
            school=self.school, first_name="Jane", last_name="Doe",
            relationship="Mother", phone_number="+254700000000",
        )
        link = StudentGuardian.objects.create(
            student=student, guardian=guardian, is_primary_contact=True
        )
        self.assertIn(guardian, student.guardians.all())
        self.assertTrue(link.is_primary_contact)

    def test_staff_id_unique_per_school(self):
        staff_user = User.objects.create_user(
            username="teacher1", password="pass12345", role=User.Role.TEACHER
        )
        Staff.objects.create(
            user=staff_user, school=self.school, staff_id="STF001",
            job_title="Mathematics Teacher", date_hired=datetime.date(2024, 1, 5),
        )
        staff_user2 = User.objects.create_user(
            username="teacher2", password="pass12345", role=User.Role.TEACHER
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Staff.objects.create(
                    user=staff_user2, school=self.school, staff_id="STF001",
                    job_title="Science Teacher", date_hired=datetime.date(2024, 2, 1),
                )


class AuthAndDashboardTests(TestCase):
    """Phase 4: login flow, role-based routing, access control."""

    def setUp(self):
        self.super_admin = User.objects.create_user(
            username="admin1", password="pass12345", role=User.Role.SUPER_ADMIN
        )
        self.teacher = User.objects.create_user(
            username="teach1", password="pass12345", role=User.Role.TEACHER
        )

    def test_login_creates_login_history_and_audit_log(self):
        self.client.post(
            reverse("dashboard:login"),
            {"username": "admin1", "password": "pass12345"},
        )
        self.assertEqual(LoginHistory.objects.filter(user=self.super_admin).count(), 1)
        self.assertTrue(
            LoginHistory.objects.get(user=self.super_admin).was_successful
        )
        self.assertTrue(
            AuditLog.objects.filter(
                actor=self.super_admin, action=AuditLog.Action.LOGIN
            ).exists()
        )

    def test_failed_login_recorded(self):
        self.client.post(
            reverse("dashboard:login"),
            {"username": "admin1", "password": "wrong-password"},
        )
        self.assertTrue(
            LoginHistory.objects.filter(
                user=self.super_admin, was_successful=False
            ).exists()
        )

    def test_super_admin_router_redirects_to_super_admin_dashboard(self):
        self.client.login(username="admin1", password="pass12345")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:super_admin"))

    def test_non_super_admin_router_redirects_to_coming_soon(self):
        # Teacher (Phase 18), Finance Admin/Accountant (Phase 19), Staff
        # Admin (Phase 20), and Academic Admin (Phase 21) now all have
        # real dashboards — use a role that genuinely has no dashboard
        # built yet (Department Head) to test the fallback path.
        department_head = User.objects.create_user(
            username="depthead1", password="pass12345", role=User.Role.DEPARTMENT_HEAD
        )
        self.client.login(username="depthead1", password="pass12345")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:coming_soon"))

    def test_teacher_cannot_access_super_admin_dashboard(self):
        self.client.login(username="teach1", password="pass12345")
        response = self.client.get(reverse("dashboard:super_admin"))
        self.assertEqual(response.status_code, 403)

    def test_super_admin_dashboard_shows_live_student_count(self):
        school = School.objects.create(name="Riverside High", code="RVH2")
        student_user = User.objects.create_user(
            username="stud1", password="pass12345", role=User.Role.STUDENT
        )
        Student.objects.create(
            user=student_user, school=school, admission_number="ADM100",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.client.login(username="admin1", password="pass12345")
        response = self.client.get(reverse("dashboard:super_admin"))
        self.assertEqual(response.context["total_students"], 1)

    def test_super_admin_sidebar_sections_are_available(self):
        self.client.login(username="admin1", password="pass12345")
        for name in ("super_admin_users", "super_admin_school_config", "super_admin_audit_logs"):
            response = self.client.get(reverse(f"dashboard:{name}"))
            self.assertEqual(response.status_code, 200)

    def test_super_admin_can_update_user_role_and_audit_is_recorded(self):
        self.client.login(username="admin1", password="pass12345")
        response = self.client.post(
            reverse("dashboard:super_admin_users"),
            {"user_id": self.teacher.pk, "action": "role", "role": User.Role.CLASS_TEACHER},
        )
        self.assertRedirects(response, reverse("dashboard:super_admin_users"))
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.role, User.Role.CLASS_TEACHER)
        self.assertTrue(
            AuditLog.objects.filter(
                target_model="User", target_object_id=str(self.teacher.pk),
                action=AuditLog.Action.ROLE_CHANGE,
            ).exists()
        )

    def test_super_admin_can_update_school_configuration(self):
        school = School.objects.create(name="Old School", code="OLD")
        self.client.login(username="admin1", password="pass12345")
        response = self.client.post(
            reverse("dashboard:super_admin_school_config"),
            {"name": "New School", "code": "NEW", "motto": "Learn", "enable_position_ranking": "on"},
        )
        self.assertRedirects(response, reverse("dashboard:super_admin_school_config"))
        school.refresh_from_db()
        self.assertEqual(school.name, "New School")
        self.assertTrue(school.enable_position_ranking)

    def test_locked_account_cannot_reach_dashboard(self):
        self.super_admin.is_locked = True
        self.super_admin.save()
        self.client.login(username="admin1", password="pass12345")
        response = self.client.get(reverse("dashboard:super_admin"))
        self.assertRedirects(response, reverse("dashboard:account_locked"))

    def test_anonymous_user_redirected_to_login(self):
        response = self.client.get(reverse("dashboard:super_admin"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard:login"), response.url)

    def test_anonymous_user_hitting_root_redirected_not_500(self):
        """Regression test: DashboardRouterView must reject anonymous users
        before touching request.user.role, or it 500s with AttributeError
        on AnonymousUser."""
        response = self.client.get(reverse("dashboard:home"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("dashboard:login"), response.url)

    def test_logout_link_ends_session_and_redirects_to_login(self):
        self.client.login(username="admin1", password="pass12345")
        response = self.client.get(reverse("dashboard:logout"))

        self.assertRedirects(response, reverse("dashboard:login"))
        dashboard_response = self.client.get(reverse("dashboard:super_admin"))
        self.assertRedirects(
            dashboard_response,
            f"{reverse('dashboard:login')}?next={reverse('dashboard:super_admin')}",
        )

    def test_createsuperuser_account_gets_super_admin_role_and_dashboard(self):
        """Regression test: `createsuperuser` sets is_superuser=True but has
        no concept of our custom `role` field, so it would otherwise stay at
        the STUDENT default and misroute to the coming-soon page."""
        superuser = User.objects.create_superuser(
            username="rootadmin", email="root@example.com", password="pass12345"
        )
        self.assertEqual(superuser.role, User.Role.SUPER_ADMIN)

        self.client.login(username="rootadmin", password="pass12345")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:super_admin"))


class CurriculumModelTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )
        self.subject = Subject.objects.create(
            school=self.school, code="MATH", name="Mathematics"
        )
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )

    def test_subject_code_unique_per_school_not_globally(self):
        school2 = School.objects.create(name="Lakeside Academy", code="LKA")
        Subject.objects.create(school=school2, code="MATH", name="Mathematics")  # allowed
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Subject.objects.create(school=self.school, code="MATH", name="Maths Duplicate")

    def test_class_subject_pair_unique(self):
        ClassSubject.objects.create(class_group=self.class_group, subject=self.subject)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClassSubject.objects.create(class_group=self.class_group, subject=self.subject)

    def test_only_one_teacher_per_class_subject_per_term(self):
        class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        teacher_user1 = User.objects.create_user(
            username="t1", password="pass12345", role=User.Role.TEACHER
        )
        staff1 = Staff.objects.create(
            user=teacher_user1, school=self.school, staff_id="STF010",
            job_title="Maths Teacher", date_hired=datetime.date(2024, 1, 1),
        )
        TeachingAssignment.objects.create(
            class_subject=class_subject, teacher=staff1, term=self.term
        )
        teacher_user2 = User.objects.create_user(
            username="t2", password="pass12345", role=User.Role.TEACHER
        )
        staff2 = Staff.objects.create(
            user=teacher_user2, school=self.school, staff_id="STF011",
            job_title="Maths Teacher 2", date_hired=datetime.date(2024, 1, 1),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TeachingAssignment.objects.create(
                    class_subject=class_subject, teacher=staff2, term=self.term
                )

    def test_student_cannot_double_enroll_same_subject_same_year(self):
        class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        student_user = User.objects.create_user(
            username="s10", password="pass12345", role=User.Role.STUDENT
        )
        student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM200",
            admission_date=datetime.date(2026, 1, 10),
        )
        Enrollment.objects.create(
            student=student, class_subject=class_subject, academic_year=self.academic_year
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Enrollment.objects.create(
                    student=student, class_subject=class_subject,
                    academic_year=self.academic_year,
                )

    def test_enrollment_default_status_is_enrolled(self):
        class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject
        )
        student_user = User.objects.create_user(
            username="s11", password="pass12345", role=User.Role.STUDENT
        )
        student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM201",
            admission_date=datetime.date(2026, 1, 10),
        )
        enrollment = Enrollment.objects.create(
            student=student, class_subject=class_subject, academic_year=self.academic_year
        )
        self.assertEqual(enrollment.status, Enrollment.Status.ENROLLED)

    def test_subject_prerequisites_are_not_symmetrical(self):
        advanced = Subject.objects.create(
            school=self.school, code="MATH2", name="Advanced Mathematics"
        )
        advanced.prerequisites.add(self.subject)
        self.assertIn(self.subject, advanced.prerequisites.all())
        self.assertNotIn(advanced, self.subject.prerequisites.all())


class AttendanceModelTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )
        self.subject = Subject.objects.create(
            school=self.school, code="MATH", name="Mathematics"
        )
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
        self.admin_user = User.objects.create_user(
            username="acadadmin", password="pass12345", role=User.Role.ACADEMIC_ADMIN
        )
        student_user = User.objects.create_user(
            username="s20", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM300",
            admission_date=datetime.date(2026, 1, 10),
        )

    def test_only_one_session_per_class_subject_per_date(self):
        AttendanceSession.objects.create(
            class_subject=self.class_subject, term=self.term, date=datetime.date(2026, 2, 1)
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AttendanceSession.objects.create(
                    class_subject=self.class_subject, term=self.term,
                    date=datetime.date(2026, 2, 1),
                )

    def test_only_one_record_per_student_per_session(self):
        session = AttendanceSession.objects.create(
            class_subject=self.class_subject, term=self.term, date=datetime.date(2026, 2, 2)
        )
        AttendanceRecord.objects.create(
            session=session, student=self.student, status=AttendanceRecord.Status.PRESENT
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AttendanceRecord.objects.create(
                    session=session, student=self.student,
                    status=AttendanceRecord.Status.ABSENT,
                )

    def test_correct_attendance_record_updates_status_and_writes_audit_log(self):
        session = AttendanceSession.objects.create(
            class_subject=self.class_subject, term=self.term, date=datetime.date(2026, 2, 3)
        )
        record = AttendanceRecord.objects.create(
            session=session, student=self.student, status=AttendanceRecord.Status.ABSENT
        )
        correct_attendance_record(
            record=record, new_status=AttendanceRecord.Status.PRESENT,
            corrected_by=self.admin_user, new_notes="Was marked absent by mistake",
        )
        record.refresh_from_db()
        self.assertEqual(record.status, AttendanceRecord.Status.PRESENT)
        self.assertEqual(record.recorded_by, self.admin_user)

        audit_entry = AuditLog.objects.filter(
            target_model="AttendanceRecord", target_object_id=str(record.pk)
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.previous_value["status"], "ABSENT")
        self.assertEqual(audit_entry.new_value["status"], "PRESENT")


class GradingEngineModelTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10"
        )
        self.subject = Subject.objects.create(
            school=self.school, code="MATH", name="Mathematics"
        )
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
        self.structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term, name="Standard Term Structure"
        )
        self.cat_type = AssessmentType.objects.create(
            school=self.school, name="CAT", code="CAT"
        )
        self.final_type = AssessmentType.objects.create(
            school=self.school, name="Final Exam", code="FINAL"
        )
        self.cat_component = AssessmentComponent.objects.create(
            structure=self.structure, assessment_type=self.cat_type,
            weight_percentage=Decimal("30.00"), max_marks=Decimal("30"),
        )
        self.final_component = AssessmentComponent.objects.create(
            structure=self.structure, assessment_type=self.final_type,
            weight_percentage=Decimal("70.00"), max_marks=Decimal("100"),
        )
        student_user = User.objects.create_user(
            username="s30", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM400",
            admission_date=datetime.date(2026, 1, 10),
        )

    def test_component_weight_must_be_between_0_and_100(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssessmentComponent.objects.create(
                    structure=self.structure, assessment_type=self.cat_type,
                    weight_percentage=Decimal("150.00"),
                )

    def test_only_one_component_per_assessment_type_per_structure(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssessmentComponent.objects.create(
                    structure=self.structure, assessment_type=self.cat_type,
                    weight_percentage=Decimal("10.00"),
                )

    def test_only_one_default_grading_scheme_per_school(self):
        scheme1 = GradingScheme.objects.create(
            school=self.school, name="Scheme A", is_default=True
        )
        scheme2 = GradingScheme.objects.create(
            school=self.school, name="Scheme B", is_default=True
        )
        scheme1.refresh_from_db()
        self.assertFalse(scheme1.is_default)
        self.assertTrue(scheme2.is_default)

    def test_grade_band_max_must_be_gte_min(self):
        scheme = GradingScheme.objects.create(school=self.school, name="Bad Scheme")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                GradeBand.objects.create(
                    scheme=scheme, min_mark=Decimal("80"), max_mark=Decimal("50"),
                    grade="A", grade_point=Decimal("4.0"),
                )

    def test_get_grade_for_mark_resolves_correct_band(self):
        scheme = GradingScheme.objects.create(school=self.school, name="Standard")
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("80"), max_mark=Decimal("100"),
            grade="A", grade_point=Decimal("4.0"),
        )
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("70"), max_mark=Decimal("79.99"),
            grade="B", grade_point=Decimal("3.0"),
        )
        band = get_grade_for_mark(scheme, Decimal("85"))
        self.assertEqual(band.grade, "A")
        band2 = get_grade_for_mark(scheme, Decimal("75"))
        self.assertEqual(band2.grade, "B")
        self.assertIsNone(get_grade_for_mark(scheme, Decimal("40")))

    def test_validate_grade_bands_no_overlap_detects_overlap(self):
        scheme = GradingScheme.objects.create(school=self.school, name="Overlapping")
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("70"), max_mark=Decimal("100"),
            grade="A", grade_point=Decimal("4.0"),
        )
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("60"), max_mark=Decimal("75"),
            grade="B", grade_point=Decimal("3.0"),
        )
        errors = validate_grade_bands_no_overlap(scheme)
        self.assertEqual(len(errors), 1)

    def test_compute_weighted_average_uses_configured_weights_not_hardcoded(self):
        cat_assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.cat_component,
            title="CAT 1",
        )
        final_assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.final_component,
            title="Final Exam",
        )
        AssessmentMark.objects.create(
            assessment=cat_assessment, student=self.student, marks_obtained=Decimal("24"),
        )  # 24/30 -> 80% of 30% weight = 24.0
        AssessmentMark.objects.create(
            assessment=final_assessment, student=self.student, marks_obtained=Decimal("70"),
        )  # 70/100 -> 70% of 70% weight = 49.0

        result = compute_weighted_average(self.student, self.class_subject, self.term)
        self.assertEqual(result["weighted_total"], Decimal("73.00"))
        self.assertTrue(result["is_complete"])

    def test_compute_weighted_average_flags_incomplete_when_marks_missing(self):
        Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.cat_component,
            title="CAT 1",
        )
        Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.final_component,
            title="Final Exam",
        )
        result = compute_weighted_average(self.student, self.class_subject, self.term)
        self.assertFalse(result["is_complete"])
        self.assertEqual(result["components_graded"], 0)

    def test_assessment_mark_unique_per_assessment_per_student(self):
        assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=self.cat_component,
            title="CAT 1",
        )
        AssessmentMark.objects.create(
            assessment=assessment, student=self.student, marks_obtained=Decimal("20"),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AssessmentMark.objects.create(
                    assessment=assessment, student=self.student, marks_obtained=Decimal("25"),
                )

    def test_school_ranking_toggle_defaults_to_enabled(self):
        self.assertTrue(self.school.enable_position_ranking)


class ResultWorkflowTests(TestCase):
    def setUp(self):
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
        structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term, name="Standard"
        )
        final_type = AssessmentType.objects.create(school=self.school, name="Final", code="FINAL")
        component = AssessmentComponent.objects.create(
            structure=structure, assessment_type=final_type,
            weight_percentage=Decimal("100.00"), max_marks=Decimal("100"),
        )
        self.assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=component,
            title="Final Exam",
        )
        self.teacher_user = User.objects.create_user(
            username="wfteacher", password="pass12345", role=User.Role.TEACHER
        )
        self.class_teacher_user = User.objects.create_user(
            username="wfclassteacher", password="pass12345", role=User.Role.CLASS_TEACHER
        )
        self.academic_admin_user = User.objects.create_user(
            username="wfacadadmin", password="pass12345", role=User.Role.ACADEMIC_ADMIN
        )
        student_user = User.objects.create_user(
            username="wfstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM500",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.mark = AssessmentMark.objects.create(
            assessment=self.assessment, student=self.student, marks_obtained=Decimal("70"),
        )

    def _advance_to_verified(self):
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.SUBMITTED,
            actor=self.teacher_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.REVIEWED,
            actor=self.class_teacher_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.VERIFIED,
            actor=self.academic_admin_user,
        )

    def test_workflow_cannot_skip_stages(self):
        with self.assertRaises(ValueError):
            transition_assessment_workflow(
                assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
                actor=self.academic_admin_user,
            )

    def test_full_workflow_publishes_and_sets_is_published(self):
        self._advance_to_verified()
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
            actor=self.academic_admin_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.PUBLISHED,
            actor=self.academic_admin_user,
        )
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.workflow_status, Assessment.WorkflowStatus.PUBLISHED)
        self.assertTrue(self.assessment.is_published)

    def test_teacher_cannot_approve_own_submitted_assessment(self):
        self._advance_to_verified()
        with self.assertRaises(PermissionError):
            transition_assessment_workflow(
                assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
                actor=self.teacher_user,  # same user who submitted it
            )

    def test_reject_returns_assessment_to_draft(self):
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.SUBMITTED,
            actor=self.teacher_user,
        )
        reject_assessment(
            assessment=self.assessment, actor=self.academic_admin_user,
            reason="Marks look incomplete",
        )
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.workflow_status, Assessment.WorkflowStatus.DRAFT)

    def test_cannot_modify_published_mark_directly_without_amendment(self):
        """Spec §14 'Once published, prevent unrestricted modification' is
        enforced in AssessmentMark.save() — a direct edit must raise, not
        just be discouraged by convention."""
        self._advance_to_verified()
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
            actor=self.academic_admin_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.PUBLISHED,
            actor=self.academic_admin_user,
        )
        self.mark.marks_obtained = Decimal("99")
        with self.assertRaises(ValueError):
            self.mark.save()

        # The mark must be unchanged in the database.
        self.mark.refresh_from_db()
        self.assertEqual(self.mark.marks_obtained, Decimal("70"))

    def test_amendment_request_still_works_after_publish_lock(self):
        self._advance_to_verified()
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.APPROVED,
            actor=self.academic_admin_user,
        )
        transition_assessment_workflow(
            assessment=self.assessment, to_status=Assessment.WorkflowStatus.PUBLISHED,
            actor=self.academic_admin_user,
        )
        amendment = request_result_amendment(
            assessment_mark=self.mark, reason="Marking error found on recheck",
            proposed_mark=Decimal("75"), requested_by=self.teacher_user,
        )
        self.assertEqual(amendment.original_mark, Decimal("70"))
        self.assertEqual(amendment.status, ResultAmendmentRequest.Status.PENDING)

    def test_approving_amendment_updates_mark_and_logs_audit(self):
        amendment = request_result_amendment(
            assessment_mark=self.mark, reason="Marking error found on recheck",
            proposed_mark=Decimal("75"), requested_by=self.teacher_user,
        )
        decide_result_amendment(
            amendment=amendment, approve=True, reviewed_by=self.academic_admin_user,
            comment="Confirmed with answer sheet",
        )
        self.mark.refresh_from_db()
        amendment.refresh_from_db()
        self.assertEqual(self.mark.marks_obtained, Decimal("75"))
        self.assertEqual(amendment.status, ResultAmendmentRequest.Status.APPROVED)

        audit_entry = AuditLog.objects.filter(
            target_model="AssessmentMark", target_object_id=str(self.mark.pk)
        ).first()
        self.assertIsNotNone(audit_entry)
        self.assertEqual(audit_entry.previous_value["marks_obtained"], "70")
        self.assertEqual(audit_entry.new_value["marks_obtained"], "75")

    def test_rejecting_amendment_leaves_mark_unchanged(self):
        amendment = request_result_amendment(
            assessment_mark=self.mark, reason="Requesting re-mark",
            proposed_mark=Decimal("90"), requested_by=self.teacher_user,
        )
        decide_result_amendment(
            amendment=amendment, approve=False, reviewed_by=self.academic_admin_user,
            comment="Marking confirmed correct as-is",
        )
        self.mark.refresh_from_db()
        self.assertEqual(self.mark.marks_obtained, Decimal("70"))

    def test_cannot_decide_same_amendment_twice(self):
        amendment = request_result_amendment(
            assessment_mark=self.mark, reason="Test", proposed_mark=Decimal("80"),
            requested_by=self.teacher_user,
        )
        decide_result_amendment(
            amendment=amendment, approve=True, reviewed_by=self.academic_admin_user,
        )
        with self.assertRaises(ValueError):
            decide_result_amendment(
                amendment=amendment, approve=True, reviewed_by=self.academic_admin_user,
            )


class ReportBookTests(TestCase):
    def setUp(self):
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
        structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term, name="Standard"
        )
        final_type = AssessmentType.objects.create(school=self.school, name="Final", code="FINAL")
        component = AssessmentComponent.objects.create(
            structure=structure, assessment_type=final_type,
            weight_percentage=Decimal("100.00"), max_marks=Decimal("100"),
        )
        assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=component,
            title="Final Exam",
        )
        scheme = GradingScheme.objects.create(school=self.school, name="Standard", is_default=True)
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("80"), max_mark=Decimal("100"),
            grade="A", grade_point=Decimal("4.0"),
        )
        GradeBand.objects.create(
            scheme=scheme, min_mark=Decimal("0"), max_mark=Decimal("79.99"),
            grade="B", grade_point=Decimal("3.0"),
        )

        student_user = User.objects.create_user(
            username="rbstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM600",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject,
            academic_year=self.academic_year,
        )
        AssessmentMark.objects.create(
            assessment=assessment, student=self.student, marks_obtained=Decimal("85"),
        )

        self.admin_user = User.objects.create_superuser(
            username="rbadmin", email="rb@rb.com", password="pass12345"
        )
        self.template = ReportTemplate.objects.create(
            school=self.school, name="Standard Report", is_default=True,
        )

    def test_render_report_html_includes_subject_and_grade(self):
        card = ReportCard.objects.create(
            student=self.student, term=self.term, template=self.template,
        )
        html = render_report_html(report_card=card)
        self.assertIn("Mathematics", html)
        self.assertIn("85.00", html)
        self.assertIn("A", html)

    def test_generate_report_pdf_creates_downloadable_file(self):
        card = ReportCard.objects.create(
            student=self.student, term=self.term, template=self.template,
        )
        generate_report_pdf(report_card=card, generated_by=self.admin_user)
        card.refresh_from_db()
        self.assertTrue(card.pdf_file.name)
        self.assertIsNotNone(card.generated_at)
        card.pdf_file.open("rb")
        header = card.pdf_file.read(4)
        card.pdf_file.close()
        self.assertEqual(header, b"%PDF")

    def test_batch_generate_creates_card_per_active_student_in_class(self):
        second_user = User.objects.create_user(
            username="rbstudent2", password="pass12345", role=User.Role.STUDENT
        )
        Student.objects.create(
            user=second_user, school=self.school, admission_number="ADM601",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        cards = generate_batch_reports(
            class_group=self.class_group, term=self.term, template=self.template,
            generated_by=self.admin_user,
        )
        self.assertEqual(len(cards), 2)
        self.assertEqual(ReportCard.objects.filter(term=self.term).count(), 2)

    def test_only_one_default_report_template_per_school(self):
        second_template = ReportTemplate.objects.create(
            school=self.school, name="Alt Report", is_default=True,
        )
        self.template.refresh_from_db()
        self.assertFalse(self.template.is_default)
        self.assertTrue(second_template.is_default)

    def test_report_card_unique_per_student_term_template(self):
        ReportCard.objects.create(
            student=self.student, term=self.term, template=self.template,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ReportCard.objects.create(
                    student=self.student, term=self.term, template=self.template,
                )


class TranscriptTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(
            school=self.school, name="BSc CS", code="BSCCS",
            program_type=Program.ProgramType.UNIVERSITY,
        )
        self.class_group = Class.objects.create(
            school=self.school, program=self.program, name="Year 1"
        )
        self.subject_math = Subject.objects.create(
            school=self.school, code="MATH101", name="Calculus I", credit_hours=Decimal("4.0")
        )
        self.subject_cs = Subject.objects.create(
            school=self.school, code="CS101", name="Intro to CS", credit_hours=Decimal("3.0")
        )
        self.class_subject_math = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject_math
        )
        self.class_subject_cs = ClassSubject.objects.create(
            class_group=self.class_group, subject=self.subject_cs
        )
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term1 = Term.objects.create(
            academic_year=self.academic_year, name="Semester 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        self.term2 = Term.objects.create(
            academic_year=self.academic_year, name="Semester 2", term_number=2,
            start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2026, 8, 1),
        )
        self.scheme = GradingScheme.objects.create(
            school=self.school, name="4.0 Scale", is_default=True
        )
        GradeBand.objects.create(
            scheme=self.scheme, min_mark=Decimal("90"), max_mark=Decimal("100"),
            grade="A", grade_point=Decimal("4.0"),
        )
        GradeBand.objects.create(
            scheme=self.scheme, min_mark=Decimal("80"), max_mark=Decimal("89.99"),
            grade="B", grade_point=Decimal("3.0"),
        )
        GradeBand.objects.create(
            scheme=self.scheme, min_mark=Decimal("0"), max_mark=Decimal("79.99"),
            grade="C", grade_point=Decimal("2.0"),
        )

        student_user = User.objects.create_user(
            username="tstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM700",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject_math,
            academic_year=self.academic_year,
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject_cs,
            academic_year=self.academic_year,
        )

        structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term1, name="Standard"
        )
        final_type = AssessmentType.objects.create(school=self.school, name="Final", code="FINAL")
        component = AssessmentComponent.objects.create(
            structure=structure, assessment_type=final_type,
            weight_percentage=Decimal("100.00"), max_marks=Decimal("100"),
        )

        # Term 1: Calculus I (credit=4) scores 95 -> grade A (4.0);
        # published so it counts toward the transcript.
        math_assessment_t1 = Assessment.objects.create(
            class_subject=self.class_subject_math, term=self.term1, component=component,
            title="Final Exam", workflow_status=Assessment.WorkflowStatus.PUBLISHED,
        )
        AssessmentMark.objects.create(
            assessment=math_assessment_t1, student=self.student, marks_obtained=Decimal("95"),
        )

        # Term 2: Intro to CS (credit=3) scores 85 -> grade B (3.0).
        component2 = AssessmentComponent.objects.create(
            structure=AssessmentStructure.objects.create(
                school=self.school, term=self.term2, name="Standard"
            ),
            assessment_type=final_type,
            weight_percentage=Decimal("100.00"), max_marks=Decimal("100"),
        )
        cs_assessment_t2 = Assessment.objects.create(
            class_subject=self.class_subject_cs, term=self.term2, component=component2,
            title="Final Exam", workflow_status=Assessment.WorkflowStatus.PUBLISHED,
        )
        AssessmentMark.objects.create(
            assessment=cs_assessment_t2, student=self.student, marks_obtained=Decimal("85"),
        )

        self.admin_user = User.objects.create_superuser(
            username="transcriptadmin", email="ta@ta.com", password="pass12345"
        )

    def test_generate_transcript_snapshots_only_published_entries(self):
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        self.assertEqual(transcript.entries.count(), 2)
        subject_names = set(transcript.entries.values_list("subject_name", flat=True))
        self.assertEqual(subject_names, {"Calculus I", "Intro to CS"})

    def test_transcript_excludes_unpublished_assessments(self):
        structure = AssessmentStructure.objects.get(term=self.term1)
        draft_component = AssessmentComponent.objects.create(
            structure=structure,
            assessment_type=AssessmentType.objects.create(
                school=self.school, name="CAT", code="CAT1"
            ),
            weight_percentage=Decimal("0.00"), max_marks=Decimal("100"),
        )
        draft_assessment = Assessment.objects.create(
            class_subject=self.class_subject_math, term=self.term1, component=draft_component,
            title="Draft CAT", workflow_status=Assessment.WorkflowStatus.DRAFT,
        )
        AssessmentMark.objects.create(
            assessment=draft_assessment, student=self.student, marks_obtained=Decimal("40"),
        )
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        # Still only 2 entries: the draft assessment's term/class_subject
        # combination must not surface as a separate published entry.
        self.assertEqual(transcript.entries.count(), 2)

    def test_cgpa_is_credit_hour_weighted(self):
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        # (4.0*4 + 3.0*3) / (4+3) = 25/7 = 3.5714... -> 3.57
        self.assertEqual(transcript.cgpa, Decimal("3.57"))

    def test_transcript_pdf_is_generated_and_downloadable(self):
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        self.assertTrue(transcript.pdf_file.name)
        transcript.pdf_file.open("rb")
        header = transcript.pdf_file.read(4)
        transcript.pdf_file.close()
        self.assertEqual(header, b"%PDF")

    def test_verification_code_is_unique_and_verifiable(self):
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        result = verify_transcript(transcript.verification_code)
        self.assertTrue(result["valid"])
        self.assertEqual(result["admission_number"], "ADM700")

    def test_invalid_verification_code_returns_not_valid(self):
        import uuid
        result = verify_transcript(uuid.uuid4())
        self.assertFalse(result["valid"])

    def test_academic_status_and_graduation_status_snapshotted(self):
        self.student.status = Student.Status.GRADUATED
        self.student.save()
        transcript = generate_transcript(student=self.student, generated_by=self.admin_user)
        self.assertEqual(transcript.academic_status, Student.Status.GRADUATED)
        self.assertEqual(transcript.graduation_status, Transcript.GraduationStatus.GRADUATED)


class LMSTests(TestCase):
    def setUp(self):
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
        teacher_user = User.objects.create_user(
            username="lmsteacher", password="pass12345", role=User.Role.TEACHER
        )
        self.teacher = Staff.objects.create(
            user=teacher_user, school=self.school, staff_id="STF900",
            job_title="Maths Teacher", date_hired=datetime.date(2024, 1, 1),
        )
        student_user = User.objects.create_user(
            username="lmsstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM800",
            admission_date=datetime.date(2026, 1, 10),
        )

    # --- Assignments ---

    def test_first_submission_always_allowed(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 1",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            allow_resubmission=False,
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="My essay"
        )
        self.assertEqual(submission.attempt_number, 1)
        self.assertEqual(submission.status, AssignmentSubmission.Status.SUBMITTED)

    def test_resubmission_blocked_when_not_allowed(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 2",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            allow_resubmission=False,
        )
        submit_assignment(assignment=assignment, student=self.student, submitted_text="v1")
        with self.assertRaises(ValueError):
            submit_assignment(assignment=assignment, student=self.student, submitted_text="v2")

    def test_resubmission_allowed_increments_attempt_and_clears_grade(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 3",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            allow_resubmission=True,
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="v1"
        )
        grade_assignment_submission(
            submission=submission, marks_obtained=Decimal("70"), feedback="Good start",
            graded_by=self.teacher,
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="v2"
        )
        self.assertEqual(submission.attempt_number, 2)
        self.assertEqual(submission.status, AssignmentSubmission.Status.RESUBMITTED)
        self.assertIsNone(submission.marks_obtained)

    def test_late_submission_flagged(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 4",
            deadline=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="Late work"
        )
        self.assertTrue(submission.is_late)

    def test_grade_cannot_exceed_max_marks(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay 5",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            max_marks=Decimal("50"),
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="v1"
        )
        with self.assertRaises(ValueError):
            grade_assignment_submission(
                submission=submission, marks_obtained=Decimal("75"), feedback="",
                graded_by=self.teacher,
            )

    # --- Quizzes ---

    def _make_quiz_with_questions(self):
        quiz = Quiz.objects.create(
            class_subject=self.class_subject, term=self.term, title="Quiz 1"
        )
        mcq = QuizQuestion.objects.create(
            quiz=quiz, question_text="2 + 2 = ?",
            question_type=QuizQuestion.QuestionType.MULTIPLE_CHOICE, marks=Decimal("2"),
        )
        correct_opt = QuizOption.objects.create(question=mcq, option_text="4", is_correct=True)
        QuizOption.objects.create(question=mcq, option_text="5", is_correct=False)

        multi = QuizQuestion.objects.create(
            quiz=quiz, question_text="Select all primes",
            question_type=QuizQuestion.QuestionType.MULTIPLE_ANSWER, marks=Decimal("3"),
        )
        prime2 = QuizOption.objects.create(question=multi, option_text="2", is_correct=True)
        prime3 = QuizOption.objects.create(question=multi, option_text="3", is_correct=True)
        QuizOption.objects.create(question=multi, option_text="4", is_correct=False)

        short = QuizQuestion.objects.create(
            quiz=quiz, question_text="Explain your reasoning",
            question_type=QuizQuestion.QuestionType.SHORT_ANSWER, marks=Decimal("5"),
        )
        return quiz, mcq, correct_opt, multi, prime2, prime3, short

    def test_mcq_auto_grades_correctly(self):
        quiz, mcq, correct_opt, multi, prime2, prime3, short = self._make_quiz_with_questions()
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student)
        submit_quiz_attempt(
            attempt=attempt,
            answers={
                mcq.pk: {"option_ids": [correct_opt.pk]},
                multi.pk: {"option_ids": [prime2.pk, prime3.pk]},
                short.pk: {"text": "Because math."},
            },
        )
        attempt.refresh_from_db()
        # MCQ (2) + multi-answer exact match (3) = 5; short answer ungraded.
        self.assertEqual(attempt.auto_score, Decimal("5"))
        self.assertFalse(attempt.is_fully_graded)

    def test_multiple_answer_requires_exact_set_match(self):
        quiz, mcq, correct_opt, multi, prime2, prime3, short = self._make_quiz_with_questions()
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student)
        submit_quiz_attempt(
            attempt=attempt,
            answers={
                mcq.pk: {"option_ids": [correct_opt.pk]},
                # Only one of two correct primes selected -> incorrect, 0 marks.
                multi.pk: {"option_ids": [prime2.pk]},
                short.pk: {"text": "..."},
            },
        )
        attempt.refresh_from_db()
        self.assertEqual(attempt.auto_score, Decimal("2"))  # only the MCQ mark

    def test_short_answer_requires_manual_grading_to_finalize(self):
        quiz, mcq, correct_opt, multi, prime2, prime3, short = self._make_quiz_with_questions()
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student)
        submit_quiz_attempt(
            attempt=attempt,
            answers={
                mcq.pk: {"option_ids": [correct_opt.pk]},
                multi.pk: {"option_ids": [prime2.pk, prime3.pk]},
                short.pk: {"text": "Because math."},
            },
        )
        attempt.refresh_from_db()
        self.assertFalse(attempt.is_fully_graded)

        short_answer = attempt.answers.get(question=short)
        grade_quiz_short_answer(answer=short_answer, marks_awarded=Decimal("4"))
        attempt.refresh_from_db()
        self.assertTrue(attempt.is_fully_graded)
        self.assertEqual(attempt.manual_score, Decimal("4"))
        self.assertEqual(attempt.total_score, Decimal("9"))  # 5 auto + 4 manual

    def test_short_answer_grade_cannot_exceed_question_marks(self):
        quiz, mcq, correct_opt, multi, prime2, prime3, short = self._make_quiz_with_questions()
        attempt = QuizAttempt.objects.create(quiz=quiz, student=self.student)
        submit_quiz_attempt(
            attempt=attempt,
            answers={
                mcq.pk: {"option_ids": [correct_opt.pk]},
                multi.pk: {"option_ids": [prime2.pk, prime3.pk]},
                short.pk: {"text": "..."},
            },
        )
        short_answer = attempt.answers.get(question=short)
        with self.assertRaises(ValueError):
            grade_quiz_short_answer(answer=short_answer, marks_awarded=Decimal("10"))

    def test_only_one_attempt_number_per_quiz_student(self):
        quiz, *_ = self._make_quiz_with_questions()
        QuizAttempt.objects.create(quiz=quiz, student=self.student, attempt_number=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                QuizAttempt.objects.create(quiz=quiz, student=self.student, attempt_number=1)

    # --- Discussions ---

    def test_discussion_and_reply_creation(self):
        discussion = Discussion.objects.create(
            class_subject=self.class_subject, term=self.term,
            thread_type=Discussion.ThreadType.ANNOUNCEMENT,
            title="Midterm postponed", created_by=self.teacher.user,
        )
        DiscussionReply.objects.create(
            discussion=discussion, author=self.student.user, content="Thanks for the update!"
        )
        self.assertEqual(discussion.replies.count(), 1)


class FinanceTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        self.tuition = FeeCategory.objects.create(
            school=self.school, name="Tuition", code="TUITION"
        )
        self.transport = FeeCategory.objects.create(
            school=self.school, name="Transport", code="TRANSPORT"
        )
        self.structure = FeeStructure.objects.create(
            school=self.school, academic_year=self.academic_year, term=self.term,
            name="Term 1 Fees",
        )
        FeeStructureItem.objects.create(
            structure=self.structure, category=self.tuition, amount=Decimal("50000")
        )
        FeeStructureItem.objects.create(
            structure=self.structure, category=self.transport, amount=Decimal("10000")
        )

        student_user = User.objects.create_user(
            username="financestudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM900",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.finance_admin = User.objects.create_user(
            username="financeadmin", password="pass12345", role=User.Role.FINANCE_ADMIN
        )

    def test_generate_invoice_sums_structure_items(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(invoice.total_amount, Decimal("60000"))
        self.assertEqual(invoice.line_items.count(), 2)
        self.assertEqual(invoice.status, Invoice.Status.UNPAID)
        self.assertTrue(invoice.invoice_number.startswith("INV-"))

    def test_percentage_concession_reduces_invoice_total(self):
        FeeConcession.objects.create(
            student=self.student, academic_year=self.academic_year,
            concession_type=FeeConcession.ConcessionType.SCHOLARSHIP,
            percentage=Decimal("20.00"),
        )
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        # 60000 - 20% = 48000
        self.assertEqual(invoice.total_amount, Decimal("48000.00"))
        scholarship_line = invoice.line_items.get(
            line_type=FeeConcession.ConcessionType.SCHOLARSHIP
        )
        self.assertEqual(scholarship_line.amount, Decimal("12000.00"))

    def test_fixed_amount_concession_applied(self):
        FeeConcession.objects.create(
            student=self.student, academic_year=self.academic_year,
            concession_type=FeeConcession.ConcessionType.WAIVER,
            fixed_amount=Decimal("5000"),
        )
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(invoice.total_amount, Decimal("55000"))

    def test_concession_cannot_push_invoice_negative(self):
        FeeConcession.objects.create(
            student=self.student, academic_year=self.academic_year,
            concession_type=FeeConcession.ConcessionType.WAIVER,
            fixed_amount=Decimal("999999"),
        )
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        self.assertEqual(invoice.total_amount, Decimal("0"))

    def test_partial_payment_sets_invoice_partially_paid_and_generates_receipt(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        payment = record_payment(
            invoice=invoice, amount=Decimal("20000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)
        self.assertTrue(hasattr(payment, "receipt"))
        self.assertTrue(payment.receipt.receipt_number.startswith("RCT-"))

    def test_full_payment_marks_invoice_paid(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        record_payment(
            invoice=invoice, amount=Decimal("60000"), payment_method=Payment.Method.MPESA,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin, gateway_reference="QGH7XYZ123",
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

    def test_overpayment_is_rejected(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        with self.assertRaises(ValueError):
            record_payment(
                invoice=invoice, amount=Decimal("70000"), payment_method=Payment.Method.CASH,
                payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
                received_by=self.finance_admin,
            )

    def test_refund_approval_reduces_invoice_paid_status(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        payment = record_payment(
            invoice=invoice, amount=Decimal("60000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        refund = request_refund(
            payment=payment, amount=Decimal("10000"), reason="Overcharged transport fee",
            requested_by=self.finance_admin,
        )
        decide_refund(refund=refund, approve=True, decided_by=self.finance_admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)

    def test_cannot_decide_same_refund_twice(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        payment = record_payment(
            invoice=invoice, amount=Decimal("60000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        refund = request_refund(
            payment=payment, amount=Decimal("5000"), reason="Test", requested_by=self.finance_admin,
        )
        decide_refund(refund=refund, approve=True, decided_by=self.finance_admin)
        with self.assertRaises(ValueError):
            decide_refund(refund=refund, approve=True, decided_by=self.finance_admin)

    def test_financial_adjustment_changes_invoice_total_and_status(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        record_payment(
            invoice=invoice, amount=Decimal("60000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)

        apply_financial_adjustment(
            invoice=invoice, adjustment_type=FinancialAdjustment.AdjustmentType.CORRECTION,
            amount=Decimal("5000"), reason="Missed a late library fine",
            created_by=self.finance_admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.total_amount, Decimal("65000"))
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)

    def test_account_summary_reports_outstanding_and_arrears(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_admin, due_date=datetime.date(2020, 1, 1),  # overdue
        )
        record_payment(
            invoice=invoice, amount=Decimal("10000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2020, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_admin,
        )
        summary = compute_student_account_summary(student=self.student)
        self.assertEqual(summary["total_billed"], Decimal("60000"))
        self.assertEqual(summary["total_paid"], Decimal("10000"))
        self.assertEqual(summary["outstanding_balance"], Decimal("50000"))
        self.assertEqual(summary["arrears"], Decimal("60000"))  # still not PAID and overdue

    def test_invoice_line_item_amounts_are_non_negative(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                invoice = Invoice.objects.create(
                    invoice_number="INV-TEST1", student=self.student, school=self.school,
                    academic_year=self.academic_year, term=self.term,
                    fee_structure=self.structure, total_amount=Decimal("100"),
                    issue_date=datetime.date(2026, 1, 1), due_date=datetime.date(2026, 2, 1),
                )
                InvoiceLineItem.objects.create(
                    invoice=invoice, line_type="FEE", amount=Decimal("-100"),
                )


class LibraryTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        LibrarySettings.objects.create(
            school=self.school, loan_period_days=14, max_books_per_student=2,
            fine_per_day=Decimal("10.00"),
        )
        self.category = BookCategory.objects.create(school=self.school, name="Fiction")
        self.book = Book.objects.create(
            school=self.school, title="The Hobbit", isbn="9780618968633",
            category=self.category,
        )
        self.copy1 = BookCopy.objects.create(book=self.book, accession_number="ACC001")
        self.copy2 = BookCopy.objects.create(book=self.book, accession_number="ACC002")

        student_user = User.objects.create_user(
            username="librarystudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM950",
            admission_date=datetime.date(2026, 1, 10),
        )
        librarian_user = User.objects.create_user(
            username="librarian1", password="pass12345", role=User.Role.LIBRARIAN
        )
        self.librarian = Staff.objects.create(
            user=librarian_user, school=self.school, staff_id="STF950",
            job_title="Librarian", date_hired=datetime.date(2024, 1, 1),
        )

    def test_borrow_book_marks_copy_borrowed(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        self.copy1.refresh_from_db()
        self.assertEqual(self.copy1.status, BookCopy.Status.BORROWED)
        self.assertEqual(borrowing.due_date - borrowing.borrowed_date, datetime.timedelta(days=14))

    def test_cannot_borrow_unavailable_copy(self):
        borrow_book(book_copy=self.copy1, student=self.student, issued_by=self.librarian)
        student_user2 = User.objects.create_user(
            username="librarystudent2", password="pass12345", role=User.Role.STUDENT
        )
        student2 = Student.objects.create(
            user=student_user2, school=self.school, admission_number="ADM951",
            admission_date=datetime.date(2026, 1, 10),
        )
        with self.assertRaises(ValueError):
            borrow_book(book_copy=self.copy1, student=student2, issued_by=self.librarian)

    def test_borrower_must_be_exactly_one_of_student_or_staff(self):
        with self.assertRaises(ValueError):
            borrow_book(book_copy=self.copy1, issued_by=self.librarian)
        with self.assertRaises(ValueError):
            borrow_book(
                book_copy=self.copy1, student=self.student, staff=self.librarian,
                issued_by=self.librarian,
            )

    def test_student_book_limit_enforced(self):
        book2 = Book.objects.create(school=self.school, title="The Two Towers")
        copy_b2 = BookCopy.objects.create(book=book2, accession_number="ACC010")
        book3 = Book.objects.create(school=self.school, title="Return of the King")
        copy_b3 = BookCopy.objects.create(book=book3, accession_number="ACC011")

        borrow_book(book_copy=self.copy1, student=self.student, issued_by=self.librarian)
        borrow_book(book_copy=copy_b2, student=self.student, issued_by=self.librarian)
        # max_books_per_student=2 for this school -> third borrow must fail.
        with self.assertRaises(ValueError):
            borrow_book(book_copy=copy_b3, student=self.student, issued_by=self.librarian)

    def test_return_on_time_has_no_fine(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        return_book(borrowing=borrowing, returned_to=self.librarian)
        borrowing.refresh_from_db()
        self.assertEqual(borrowing.status, Borrowing.Status.RETURNED)
        self.assertEqual(borrowing.fine_amount, Decimal("0.00"))
        self.copy1.refresh_from_db()
        self.assertEqual(self.copy1.status, BookCopy.Status.AVAILABLE)

    def test_late_return_computes_fine(self):
        from django.utils import timezone

        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        # Force the due date into the past to simulate lateness. Uses the
        # same timezone.localtime(timezone.now()).date() the service uses
        # (not datetime.date.today()) — regression guard for a real bug
        # where timezone.now().date() returned the UTC calendar date
        # instead of the school's configured local date, silently causing
        # an off-by-one-day fine during Nairobi's 00:00-03:00 local window.
        today = timezone.localtime(timezone.now()).date()
        borrowing.due_date = today - datetime.timedelta(days=3)
        borrowing.save(update_fields=["due_date"])

        return_book(borrowing=borrowing, returned_to=self.librarian)
        borrowing.refresh_from_db()
        # 3 days late * 10.00/day = 30.00
        self.assertEqual(borrowing.fine_amount, Decimal("30.00"))

    def test_cannot_return_already_returned_borrowing(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        return_book(borrowing=borrowing, returned_to=self.librarian)
        with self.assertRaises(ValueError):
            return_book(borrowing=borrowing, returned_to=self.librarian)

    def test_mark_book_lost_updates_copy_status(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        mark_book_lost(borrowing=borrowing, marked_by=self.librarian)
        borrowing.refresh_from_db()
        self.copy1.refresh_from_db()
        self.assertEqual(borrowing.status, Borrowing.Status.LOST)
        self.assertEqual(self.copy1.status, BookCopy.Status.LOST)

    def test_pay_fine_requires_full_amount(self):
        borrowing = borrow_book(
            book_copy=self.copy1, student=self.student, issued_by=self.librarian,
        )
        borrowing.due_date = datetime.date.today() - datetime.timedelta(days=2)
        borrowing.save(update_fields=["due_date"])
        return_book(borrowing=borrowing, returned_to=self.librarian)
        borrowing.refresh_from_db()

        with self.assertRaises(ValueError):
            pay_library_fine(
                borrowing=borrowing, amount_paid=Decimal("5.00"), received_by=self.librarian,
            )
        pay_library_fine(
            borrowing=borrowing, amount_paid=borrowing.fine_amount, received_by=self.librarian,
        )
        borrowing.refresh_from_db()
        self.assertTrue(borrowing.fine_paid)

    def test_staff_can_also_borrow(self):
        borrowing = borrow_book(
            book_copy=self.copy2, staff=self.librarian, issued_by=self.librarian,
        )
        self.assertEqual(borrowing.staff, self.librarian)
        self.assertIsNone(borrowing.student)

    def test_active_borrowing_unique_per_copy_at_db_level(self):
        borrow_book(book_copy=self.copy1, student=self.student, issued_by=self.librarian)
        student_user2 = User.objects.create_user(
            username="librarystudent3", password="pass12345", role=User.Role.STUDENT
        )
        student2 = Student.objects.create(
            user=student_user2, school=self.school, admission_number="ADM952",
            admission_date=datetime.date(2026, 1, 10),
        )
        # Bypass the service-layer status check to confirm the DB itself
        # enforces at most one active (unreturned) borrowing per copy.
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Borrowing.objects.create(
                    book_copy=self.copy1, student=student2,
                    borrowed_date=datetime.date.today(),
                    due_date=datetime.date.today() + datetime.timedelta(days=14),
                )


class TimetableTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        self.class_a = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10A"
        )
        self.class_b = Class.objects.create(
            school=self.school, program=self.program, name="Grade 10B"
        )
        self.math = Subject.objects.create(school=self.school, code="MATH", name="Mathematics")
        self.english = Subject.objects.create(school=self.school, code="ENG", name="English")
        self.cs_math_a = ClassSubject.objects.create(class_group=self.class_a, subject=self.math)
        self.cs_english_a = ClassSubject.objects.create(class_group=self.class_a, subject=self.english)
        self.cs_math_b = ClassSubject.objects.create(class_group=self.class_b, subject=self.math)

        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )

        teacher_user = User.objects.create_user(
            username="ttteacher", password="pass12345", role=User.Role.TEACHER
        )
        self.teacher = Staff.objects.create(
            user=teacher_user, school=self.school, staff_id="STF970",
            job_title="Maths Teacher", date_hired=datetime.date(2024, 1, 1),
        )
        teacher_user2 = User.objects.create_user(
            username="ttteacher2", password="pass12345", role=User.Role.TEACHER
        )
        self.teacher2 = Staff.objects.create(
            user=teacher_user2, school=self.school, staff_id="STF971",
            job_title="English Teacher", date_hired=datetime.date(2024, 1, 1),
        )

        self.ta_math_a = TeachingAssignment.objects.create(
            class_subject=self.cs_math_a, teacher=self.teacher, term=self.term
        )
        self.ta_english_a = TeachingAssignment.objects.create(
            class_subject=self.cs_english_a, teacher=self.teacher2, term=self.term
        )
        self.ta_math_b = TeachingAssignment.objects.create(
            class_subject=self.cs_math_b, teacher=self.teacher, term=self.term
        )

        self.room1 = Room.objects.create(school=self.school, name="Room 1")
        self.period1 = Period.objects.create(
            school=self.school, name="Period 1",
            start_time=datetime.time(8, 0), end_time=datetime.time(8, 40), order=1,
        )
        self.period2 = Period.objects.create(
            school=self.school, name="Period 2",
            start_time=datetime.time(8, 40), end_time=datetime.time(9, 20), order=2,
        )

    def test_create_slot_denormalizes_term_teacher_class_group(self):
        slot = create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1, room=self.room1,
        )
        self.assertEqual(slot.term, self.term)
        self.assertEqual(slot.teacher, self.teacher)
        self.assertEqual(slot.class_group, self.class_a)

    def test_teacher_double_booking_detected(self):
        create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1,
        )
        # Same teacher (self.teacher), same day/period, different class -> conflict.
        with self.assertRaises(ValueError):
            create_timetable_slot(
                teaching_assignment=self.ta_math_b, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
                period=self.period1,
            )

    def test_room_double_booking_detected(self):
        create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1, room=self.room1,
        )
        # Different teacher/class but same room, day, period -> conflict.
        with self.assertRaises(ValueError):
            create_timetable_slot(
                teaching_assignment=self.ta_english_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
                period=self.period1, room=self.room1,
            )

    def test_class_double_booking_detected(self):
        create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1,
        )
        # Same class (class_a), same day/period, different subject/teacher -> conflict.
        with self.assertRaises(ValueError):
            create_timetable_slot(
                teaching_assignment=self.ta_english_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
                period=self.period1,
            )

    def test_no_conflict_across_different_terms(self):
        term2 = Term.objects.create(
            academic_year=self.academic_year, name="Term 2", term_number=2,
            start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2026, 8, 1),
        )
        ta_math_a_term2 = TeachingAssignment.objects.create(
            class_subject=self.cs_math_a, teacher=self.teacher, term=term2
        )
        create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1,
        )
        # Same teacher/day/period but a DIFFERENT term -> not a real conflict
        # (terms don't overlap in time), must succeed.
        slot2 = create_timetable_slot(
            teaching_assignment=ta_math_a_term2, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1,
        )
        self.assertEqual(slot2.term, term2)

    def test_no_conflict_different_day_or_period(self):
        create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1,
        )
        # Same teacher, different period -> no conflict.
        slot2 = create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period2,
        )
        self.assertIsNotNone(slot2.pk)

    def test_reschedule_moves_slot_successfully(self):
        slot = create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1,
        )
        moved = reschedule_timetable_slot(
            slot=slot, period=self.period2, changed_by=self.teacher.user,
        )
        self.assertEqual(moved.period, self.period2)
        self.assertEqual(TimetableSlot.objects.filter(teaching_assignment=self.ta_math_a).count(), 1)

    def test_reschedule_into_conflict_restores_original_slot(self):
        create_timetable_slot(
            teaching_assignment=self.ta_english_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period2,
        )
        original_slot = create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1,
        )
        with self.assertRaises(ValueError):
            # class_a already has English at period2 -> class double-booking.
            reschedule_timetable_slot(
                slot=original_slot, period=self.period2, changed_by=self.teacher.user,
            )
        # The original Monday/Period1 slot must still exist — no gap left behind.
        self.assertTrue(
            TimetableSlot.objects.filter(
                teaching_assignment=self.ta_math_a, day_of_week="MON", period=self.period1
            ).exists()
        )

    def test_db_constraint_enforces_teacher_conflict_even_bypassing_service(self):
        create_timetable_slot(
            teaching_assignment=self.ta_math_a, day_of_week=TimetableSlot.DayOfWeek.MONDAY,
            period=self.period1,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TimetableSlot.objects.create(
                    teaching_assignment=self.ta_math_b, day_of_week="MON", period=self.period1,
                )


class CommunicationTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.student_user = User.objects.create_user(
            username="commstudent", password="pass12345", role=User.Role.STUDENT,
            email="student@example.com",
        )
        self.student = Student.objects.create(
            user=self.student_user, school=self.school, admission_number="ADM960",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.teacher_user = User.objects.create_user(
            username="commteacher", password="pass12345", role=User.Role.TEACHER,
            email="teacher@example.com",
        )
        self.finance_user = User.objects.create_user(
            username="commfinance", password="pass12345", role=User.Role.FINANCE_ADMIN,
            email="finance@example.com",
        )

    def test_send_notification_creates_in_app_delivery_by_default(self):
        notification = send_notification(
            recipient=self.student_user, notification_type="OTHER",
            title="Test", body="Test body",
        )
        self.assertEqual(notification.deliveries.count(), 1)
        delivery = notification.deliveries.first()
        self.assertEqual(delivery.channel, NotificationDelivery.Channel.IN_APP)
        self.assertEqual(delivery.status, NotificationDelivery.Status.SENT)

    def test_email_channel_actually_sends_via_django_mail(self):
        from django.core import mail

        send_notification(
            recipient=self.student_user, notification_type="OTHER",
            title="Test Email", body="Test email body", channels=["EMAIL"],
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["student@example.com"])
        self.assertEqual(mail.outbox[0].subject, "Test Email")

    def test_sms_channel_marks_failed_honestly_not_silently_skipped(self):
        prefs = NotificationPreference.objects.create(
            user=self.student_user, sms_enabled=True
        )
        notification = send_notification(
            recipient=self.student_user, notification_type="OTHER",
            title="Test", body="Test", channels=["SMS"],
        )
        sms_delivery = notification.deliveries.get(channel=NotificationDelivery.Channel.SMS)
        self.assertEqual(sms_delivery.status, NotificationDelivery.Status.FAILED)
        self.assertIn("not configured", sms_delivery.error_message)

    def test_disabled_channel_is_not_attempted_at_all(self):
        # Default NotificationPreference has email_enabled=True but let's
        # explicitly disable it and confirm no delivery row is created.
        NotificationPreference.objects.create(user=self.student_user, email_enabled=False)
        notification = send_notification(
            recipient=self.student_user, notification_type="OTHER",
            title="Test", body="Test", channels=["EMAIL"],
        )
        self.assertFalse(
            notification.deliveries.filter(channel=NotificationDelivery.Channel.EMAIL).exists()
        )

    def test_mark_notification_read_sets_timestamp(self):
        notification = send_notification(
            recipient=self.student_user, notification_type="OTHER", title="Test", body="Test",
        )
        self.assertFalse(notification.is_read)
        mark_notification_read(notification=notification)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertIsNotNone(notification.read_at)

    def test_result_published_notification_matches_spec_example(self):
        notification = notify_result_published(student=self.student, subject_name="Mathematics")
        self.assertEqual(notification.message, "Your Mathematics results have been published.")

    def test_payment_received_notification_matches_spec_example(self):
        notification = notify_payment_received(
            recipient=self.finance_user, amount=Decimal("5000"), invoice_number="INV-ABC123",
        )
        self.assertIn("received", notification.message.lower())
        self.assertEqual(notification.title, "Payment Received")

    def test_report_available_skips_guardian_with_no_linked_user(self):
        guardian = Guardian.objects.create(
            school=self.school, first_name="Jane", last_name="Doe",
            relationship="Mother", phone_number="+254700000000",
        )
        result = notify_report_available(guardian=guardian, student=self.student, term="Term 2")
        self.assertIsNone(result)

    def test_create_announcement_notifies_matching_audience_only(self):
        parent_user = User.objects.create_user(
            username="commparent", password="pass12345", role=User.Role.PARENT,
            email="parent@example.com",
        )
        Guardian.objects.create(
            user=parent_user, school=self.school, first_name="Jane", last_name="Doe",
            relationship="Mother", phone_number="+254700000000",
        )

        create_announcement(
            school=self.school, title="Sports Day", body="Sports day is next Friday.",
            audience=Announcement.Audience.STUDENTS, created_by=self.teacher_user,
        )
        self.assertTrue(
            Notification.objects.filter(recipient=self.student_user, title="Sports Day").exists()
        )
        self.assertFalse(
            Notification.objects.filter(recipient=parent_user, title="Sports Day").exists()
        )

    def test_notification_delivery_unique_per_notification_channel(self):
        notification = Notification.objects.create(
            recipient=self.student_user, notification_type="OTHER", title="T", message="M",
        )
        NotificationDelivery.objects.create(
            notification=notification, channel=NotificationDelivery.Channel.IN_APP,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                NotificationDelivery.objects.create(
                    notification=notification, channel=NotificationDelivery.Channel.IN_APP,
                )


class StudentDashboardTests(TestCase):
    """Phase 16: read-only self-service views, ownership enforcement, and
    the one legitimate write action (assignment submission)."""

    def setUp(self):
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
            is_current=True,
        )

        self.student_user = User.objects.create_user(
            username="dashstudent", password="pass12345", role=User.Role.STUDENT,
        )
        self.student = Student.objects.create(
            user=self.student_user, school=self.school, admission_number="ADM990",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject,
            academic_year=self.academic_year,
        )

        # A second, unrelated student — used for ownership-boundary tests.
        self.other_student_user = User.objects.create_user(
            username="dashstudent2", password="pass12345", role=User.Role.STUDENT,
        )
        self.other_student = Student.objects.create(
            user=self.other_student_user, school=self.school, admission_number="ADM991",
            admission_date=datetime.date(2026, 1, 10),
        )

        self.client.login(username="dashstudent", password="pass12345")

    def test_overview_page_loads(self):
        response = self.client.get(reverse("dashboard:student_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.student.admission_number)

    def test_academic_page_shows_enrolled_subject(self):
        response = self.client.get(reverse("dashboard:student_academic"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mathematics")

    def test_lms_page_loads(self):
        response = self.client.get(reverse("dashboard:student_lms"))
        self.assertEqual(response.status_code, 200)

    def test_finance_page_shows_only_own_invoices(self):
        fee_category = FeeCategory.objects.create(school=self.school, name="Tuition", code="TUI")
        structure = FeeStructure.objects.create(
            school=self.school, academic_year=self.academic_year, term=self.term, name="Term 1"
        )
        FeeStructureItem.objects.create(structure=structure, category=fee_category, amount=Decimal("1000"))
        finance_admin = User.objects.create_user(
            username="dashfinance", password="pass12345", role=User.Role.FINANCE_ADMIN
        )
        my_invoice = generate_invoice_for_student(
            student=self.student, fee_structure=structure, academic_year=self.academic_year,
            term=self.term, issued_by=finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        other_invoice = generate_invoice_for_student(
            student=self.other_student, fee_structure=structure, academic_year=self.academic_year,
            term=self.term, issued_by=finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        response = self.client.get(reverse("dashboard:student_finance"))
        self.assertContains(response, my_invoice.invoice_number)
        self.assertNotContains(response, other_invoice.invoice_number)

    def test_communication_page_loads(self):
        response = self.client.get(reverse("dashboard:student_communication"))
        self.assertEqual(response.status_code, 200)

    def test_student_can_submit_assignment_via_dashboard(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
            submission_format=Assignment.SubmissionFormat.TEXT_ENTRY,
        )
        response = self.client.post(
            reverse("dashboard:student_submit_assignment", args=[assignment.pk]),
            {"submitted_text": "My essay text"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AssignmentSubmission.objects.filter(assignment=assignment, student=self.student).exists()
        )

    def test_student_cannot_view_another_students_report_card(self):
        template = ReportTemplate.objects.create(
            school=self.school, name="Standard", is_default=True
        )
        other_card = ReportCard.objects.create(
            student=self.other_student, term=self.term, template=template,
        )
        response = self.client.get(
            reverse("dashboard:report_card_html", args=[other_card.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_student_can_view_own_report_card(self):
        template = ReportTemplate.objects.create(
            school=self.school, name="Standard", is_default=True
        )
        own_card = ReportCard.objects.create(
            student=self.student, term=self.term, template=template,
        )
        response = self.client.get(
            reverse("dashboard:report_card_html", args=[own_card.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_student_cannot_download_another_students_transcript(self):
        response = self.client.get(
            reverse("dashboard:transcript_download", args=[self.other_student.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_student_can_mark_own_notification_read(self):
        notification = send_notification(
            recipient=self.student_user, notification_type="OTHER", title="T", body="M",
        )
        response = self.client.post(
            reverse("dashboard:student_mark_notification_read", args=[notification.pk])
        )
        self.assertEqual(response.status_code, 302)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_student_cannot_mark_another_students_notification_read(self):
        other_notification = send_notification(
            recipient=self.other_student_user, notification_type="OTHER", title="T", body="M",
        )
        response = self.client.post(
            reverse("dashboard:student_mark_notification_read", args=[other_notification.pk])
        )
        self.assertEqual(response.status_code, 404)
        other_notification.refresh_from_db()
        self.assertFalse(other_notification.is_read)

    def test_non_student_role_cannot_access_student_dashboard(self):
        self.client.logout()
        teacher_user = User.objects.create_user(
            username="dashteacher", password="pass12345", role=User.Role.TEACHER
        )
        self.client.login(username="dashteacher", password="pass12345")
        response = self.client.get(reverse("dashboard:student_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_router_sends_student_to_student_dashboard(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:student_dashboard"))


class ParentDashboardTests(TestCase):
    """Phase 17: multi-child aggregation, and ownership enforcement per
    child — a parent must never be able to view a child not linked to
    their own Guardian record, even by guessing a URL."""

    def setUp(self):
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
            is_current=True,
        )

        self.parent_user = User.objects.create_user(
            username="dashparent", password="pass12345", role=User.Role.PARENT,
        )
        self.guardian = Guardian.objects.create(
            user=self.parent_user, school=self.school, first_name="Jane", last_name="Doe",
            relationship="Mother", phone_number="+254700000000",
        )

        # Two children linked to this parent (spec §18 "Parent -> Child 1/2").
        self.child1_user = User.objects.create_user(
            username="parentchild1", password="pass12345", role=User.Role.STUDENT,
        )
        self.child1 = Student.objects.create(
            user=self.child1_user, school=self.school, admission_number="ADM801",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        StudentGuardian.objects.create(student=self.child1, guardian=self.guardian)

        self.child2_user = User.objects.create_user(
            username="parentchild2", password="pass12345", role=User.Role.STUDENT,
        )
        self.child2 = Student.objects.create(
            user=self.child2_user, school=self.school, admission_number="ADM802",
            admission_date=datetime.date(2026, 1, 10),
        )
        StudentGuardian.objects.create(student=self.child2, guardian=self.guardian)

        Enrollment.objects.create(
            student=self.child1, class_subject=self.class_subject,
            academic_year=self.academic_year,
        )

        # An unrelated student, NOT linked to this parent.
        unrelated_user = User.objects.create_user(
            username="unrelatedchild", password="pass12345", role=User.Role.STUDENT,
        )
        self.unrelated_student = Student.objects.create(
            user=unrelated_user, school=self.school, admission_number="ADM803",
            admission_date=datetime.date(2026, 1, 10),
        )

        self.client.login(username="dashparent", password="pass12345")

    def test_overview_shows_both_linked_children(self):
        response = self.client.get(reverse("dashboard:parent_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.child1.admission_number)
        self.assertContains(response, self.child2.admission_number)

    def test_overview_does_not_show_unrelated_student(self):
        response = self.client.get(reverse("dashboard:parent_dashboard"))
        self.assertNotContains(response, self.unrelated_student.admission_number)

    def test_can_view_own_child_academic_page(self):
        response = self.client.get(
            reverse("dashboard:parent_child_academic", args=[self.child1.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Mathematics")

    def test_cannot_view_unrelated_student_academic_page(self):
        response = self.client.get(
            reverse("dashboard:parent_child_academic", args=[self.unrelated_student.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_can_view_own_child_finance_page_scoped_to_that_child(self):
        fee_category = FeeCategory.objects.create(school=self.school, name="Tuition", code="TUI")
        structure = FeeStructure.objects.create(
            school=self.school, academic_year=self.academic_year, term=self.term, name="Term 1"
        )
        FeeStructureItem.objects.create(structure=structure, category=fee_category, amount=Decimal("1000"))
        finance_admin = User.objects.create_user(
            username="parentfinanceadmin", password="pass12345", role=User.Role.FINANCE_ADMIN
        )
        child1_invoice = generate_invoice_for_student(
            student=self.child1, fee_structure=structure, academic_year=self.academic_year,
            term=self.term, issued_by=finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        child2_invoice = generate_invoice_for_student(
            student=self.child2, fee_structure=structure, academic_year=self.academic_year,
            term=self.term, issued_by=finance_admin, due_date=datetime.date(2026, 2, 1),
        )
        response = self.client.get(
            reverse("dashboard:parent_child_finance", args=[self.child1.pk])
        )
        self.assertContains(response, child1_invoice.invoice_number)
        # Even though child2 IS a linked child, the invoice on child1's
        # page must only show child1's invoices, not a sibling's.
        self.assertNotContains(response, child2_invoice.invoice_number)

    def test_cannot_view_unrelated_student_finance_page(self):
        response = self.client.get(
            reverse("dashboard:parent_child_finance", args=[self.unrelated_student.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_can_view_own_child_report_card(self):
        template = ReportTemplate.objects.create(
            school=self.school, name="Standard", is_default=True
        )
        report_card = ReportCard.objects.create(
            student=self.child1, term=self.term, template=template,
        )
        response = self.client.get(
            reverse("dashboard:report_card_html", args=[report_card.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_cannot_view_unrelated_student_report_card(self):
        template = ReportTemplate.objects.create(
            school=self.school, name="Standard", is_default=True
        )
        other_card = ReportCard.objects.create(
            student=self.unrelated_student, term=self.term, template=template,
        )
        response = self.client.get(
            reverse("dashboard:report_card_html", args=[other_card.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_can_download_own_child_report_card_pdf(self):
        """Regression test: ReportCardPDFView's allowed_roles previously
        omitted PARENT entirely (despite the docstring claiming parity
        with ReportCardHTMLView), so a parent hit 403 before the
        ownership check ever ran. This must return 200, not 403/404."""
        template = ReportTemplate.objects.create(
            school=self.school, name="Standard", is_default=True
        )
        report_card = ReportCard.objects.create(
            student=self.child1, term=self.term, template=template,
        )
        response = self.client.get(
            reverse("dashboard:report_card_pdf", args=[report_card.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

    def test_cannot_download_unrelated_student_report_card_pdf(self):
        template = ReportTemplate.objects.create(
            school=self.school, name="Standard", is_default=True
        )
        other_card = ReportCard.objects.create(
            student=self.unrelated_student, term=self.term, template=template,
        )
        response = self.client.get(
            reverse("dashboard:report_card_pdf", args=[other_card.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_communication_page_loads_and_shows_notifications(self):
        send_notification(
            recipient=self.parent_user, notification_type="REPORT_AVAILABLE",
            title="Report Available", body="Your child's Term 2 report is available.",
        )
        response = self.client.get(reverse("dashboard:parent_communication"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Report Available")

    def test_parent_can_mark_own_notification_read(self):
        notification = send_notification(
            recipient=self.parent_user, notification_type="OTHER", title="T", body="M",
        )
        response = self.client.post(
            reverse("dashboard:parent_mark_notification_read", args=[notification.pk])
        )
        self.assertEqual(response.status_code, 302)
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_parent_cannot_mark_unrelated_notification_read(self):
        other_parent = User.objects.create_user(
            username="otherparent", password="pass12345", role=User.Role.PARENT,
        )
        other_notification = send_notification(
            recipient=other_parent, notification_type="OTHER", title="T", body="M",
        )
        response = self.client.post(
            reverse("dashboard:parent_mark_notification_read", args=[other_notification.pk])
        )
        self.assertEqual(response.status_code, 404)
        other_notification.refresh_from_db()
        self.assertFalse(other_notification.is_read)

    def test_non_parent_role_cannot_access_parent_dashboard(self):
        self.client.logout()
        self.client.login(username="parentchild1", password="pass12345")
        response = self.client.get(reverse("dashboard:parent_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_dashboard_router_sends_parent_to_parent_dashboard(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:parent_dashboard"))

    def test_guardian_with_no_linked_children_sees_empty_dashboard(self):
        lonely_parent = User.objects.create_user(
            username="lonelyparent", password="pass12345", role=User.Role.PARENT,
        )
        Guardian.objects.create(
            user=lonely_parent, school=self.school, first_name="Solo", last_name="Parent",
            relationship="Father", phone_number="+254700000001",
        )
        self.client.logout()
        self.client.login(username="lonelyparent", password="pass12345")
        response = self.client.get(reverse("dashboard:parent_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["child_rows"]), 0)


class TeacherDashboardTests(TestCase):
    """Phase 18: ownership enforcement per class/subject (a teacher must
    never manage a class they aren't assigned to), the write actions
    (attendance, marks, grading, materials, announcements), and that
    'teachers cannot approve their own results' still holds when reached
    through the dashboard."""

    def setUp(self):
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
            is_current=True,
        )

        self.teacher_user = User.objects.create_user(
            username="dashteacher1", password="pass12345", role=User.Role.TEACHER
        )
        self.teacher = Staff.objects.create(
            user=self.teacher_user, school=self.school, staff_id="STF980",
            job_title="Maths Teacher", date_hired=datetime.date(2024, 1, 1),
        )
        self.teaching_assignment = TeachingAssignment.objects.create(
            class_subject=self.class_subject, teacher=self.teacher, term=self.term
        )

        # A second class/subject this teacher is NOT assigned to.
        subject2 = Subject.objects.create(school=self.school, code="ENG", name="English")
        self.other_class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=subject2
        )

        student_user = User.objects.create_user(
            username="dashteachstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM910",
            admission_date=datetime.date(2026, 1, 10),
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject,
            academic_year=self.academic_year,
        )

        self.client.login(username="dashteacher1", password="pass12345")

    def test_overview_page_loads(self):
        response = self.client.get(reverse("dashboard:teacher_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_overview_today_slots_only_shows_todays_day(self):
        """Regression test: today_slots previously had no day_of_week
        filter at all and silently showed the teacher's entire week."""
        from django.utils import timezone

        room = Room.objects.create(school=self.school, name="Room 1")
        period = Period.objects.create(
            school=self.school, name="Period 1",
            start_time=datetime.time(8, 0), end_time=datetime.time(8, 40), order=1,
        )
        today_code = {
            0: "MON", 1: "TUE", 2: "WED", 3: "THU", 4: "FRI", 5: "SAT",
        }.get(timezone.localtime(timezone.now()).weekday())
        if today_code is None:
            self.skipTest("Test only meaningful Mon-Sat")

        today_slot = TimetableSlot.objects.create(
            teaching_assignment=self.teaching_assignment, day_of_week=today_code,
            period=period, room=room,
        )
        other_days = [d for d in ["MON", "TUE", "WED", "THU", "FRI", "SAT"] if d != today_code]
        period2 = Period.objects.create(
            school=self.school, name="Period 2",
            start_time=datetime.time(8, 40), end_time=datetime.time(9, 20), order=2,
        )
        # Need a second teaching assignment/class_subject pairing to avoid
        # the class-double-booking constraint on the same class_group.
        other_ta = TeachingAssignment.objects.create(
            class_subject=self.other_class_subject, teacher=self.teacher, term=self.term
        )
        not_today_slot = TimetableSlot.objects.create(
            teaching_assignment=other_ta, day_of_week=other_days[0], period=period2,
        )
        response = self.client.get(reverse("dashboard:teacher_dashboard"))
        today_slots = list(response.context["today_slots"])
        self.assertIn(today_slot, today_slots)
        self.assertNotIn(not_today_slot, today_slots)

    def test_classes_page_shows_owned_class_subject(self):
        response = self.client.get(reverse("dashboard:teacher_classes"))
        self.assertContains(response, "Mathematics")

    def test_roster_shows_enrolled_student(self):
        response = self.client.get(
            reverse("dashboard:teacher_class_roster", args=[self.class_subject.pk])
        )
        self.assertContains(response, self.student.admission_number)

    def test_cannot_access_roster_for_unassigned_class_subject(self):
        response = self.client.get(
            reverse("dashboard:teacher_class_roster", args=[self.other_class_subject.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_mark_attendance_via_dashboard(self):
        target_date = datetime.date(2026, 2, 2)
        response = self.client.post(
            reverse("dashboard:teacher_attendance", args=[self.class_subject.pk]),
            {
                "date": target_date.isoformat(),
                f"status_{self.student.pk}": "PRESENT",
                f"notes_{self.student.pk}": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AttendanceRecord.objects.filter(
                student=self.student, status="PRESENT", session__date=target_date,
            ).exists()
        )

    def test_attendance_form_prefills_existing_status(self):
        """Regression test: the attendance template previously had a
        broken dict-lookup ({% if existing|dictsort:"" %}) that never
        pre-selected a student's previously recorded status, risking a
        teacher accidentally overwriting correct records with blanks on
        resubmission."""
        session = AttendanceSession.objects.create(
            class_subject=self.class_subject, term=self.term, date=datetime.date(2026, 2, 3),
        )
        AttendanceRecord.objects.create(
            session=session, student=self.student, status="ABSENT", notes="Sick",
        )
        response = self.client.get(
            f"{reverse('dashboard:teacher_attendance', args=[self.class_subject.pk])}"
            f"?date=2026-02-03"
        )
        row = next(
            r for r in response.context["student_rows"] if r["student"].pk == self.student.pk
        )
        self.assertEqual(row["record"].status, "ABSENT")
        self.assertContains(response, 'value="ABSENT" selected')

    def test_cannot_mark_attendance_for_unassigned_class(self):
        response = self.client.post(
            reverse("dashboard:teacher_attendance", args=[self.other_class_subject.pk]),
            {"date": "2026-02-02"},
        )
        self.assertEqual(response.status_code, 404)

    def test_create_assignment_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:teacher_assignments"),
            {
                "class_subject_id": self.class_subject.pk, "title": "Essay 1",
                "deadline": "2026-03-01T23:59", "max_marks": "100",
                "submission_format": Assignment.SubmissionFormat.TEXT_ENTRY,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Assignment.objects.filter(title="Essay 1").exists())

    def test_cannot_create_assignment_for_unassigned_class(self):
        response = self.client.post(
            reverse("dashboard:teacher_assignments"),
            {
                "class_subject_id": self.other_class_subject.pk, "title": "Essay 2",
                "deadline": "2026-03-01T23:59",
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_grade_submission_via_dashboard(self):
        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
        )
        submission = submit_assignment(
            assignment=assignment, student=self.student, submitted_text="My essay",
        )
        response = self.client.post(
            reverse("dashboard:teacher_assignment_submissions", args=[assignment.pk]),
            {"submission_id": submission.pk, "marks_obtained": "85", "feedback": "Great work"},
        )
        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        self.assertEqual(submission.marks_obtained, Decimal("85"))

    def test_cannot_grade_submission_for_unassigned_class_assignment(self):
        other_assignment = Assignment.objects.create(
            class_subject=self.other_class_subject, term=self.term, title="Other Essay",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
        )
        response = self.client.get(
            reverse("dashboard:teacher_assignment_submissions", args=[other_assignment.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_material_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:teacher_materials"),
            {
                "class_subject_id": self.class_subject.pk,
                "material_type": CourseMaterial.MaterialType.TEXT_LESSON,
                "title": "Intro to Algebra", "text_content": "x + 1 = 2",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CourseMaterial.objects.filter(title="Intro to Algebra").exists())

    def test_upload_material_with_valid_pdf_is_validated_and_renamed(self):
        """Regression test: file_validation.validate_upload() existed but
        was never actually called from any view — every upload endpoint
        accepted files with zero validation. Confirms a real PDF passes
        and is renamed to a UUID (spec §29 'never trust filenames
        supplied by users'), not stored under the user-supplied name."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        pdf_bytes = b"%PDF-1.4\n" + b"0" * 100
        upload = SimpleUploadedFile(
            "../../etc/passwd.pdf", pdf_bytes, content_type="application/pdf"
        )
        response = self.client.post(
            reverse("dashboard:teacher_materials"),
            {
                "class_subject_id": self.class_subject.pk,
                "material_type": CourseMaterial.MaterialType.PDF,
                "title": "Syllabus", "file": upload,
            },
        )
        self.assertEqual(response.status_code, 302)
        material = CourseMaterial.objects.get(title="Syllabus")
        self.assertNotIn("passwd", material.file.name)
        self.assertNotIn("..", material.file.name)
        self.assertTrue(material.file.name.endswith(".pdf"))

    def test_upload_material_rejects_disguised_executable(self):
        """A file with a .pdf extension but non-PDF content (sniffed via
        libmagic, not trusted from the extension or browser Content-Type)
        must be rejected."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        fake_pdf = SimpleUploadedFile(
            "malware.pdf", b"MZ\x90\x00" + b"\x00" * 100,  # PE/EXE header
            content_type="application/pdf",
        )
        response = self.client.post(
            reverse("dashboard:teacher_materials"),
            {
                "class_subject_id": self.class_subject.pk,
                "material_type": CourseMaterial.MaterialType.PDF,
                "title": "Malicious", "file": fake_pdf,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CourseMaterial.objects.filter(title="Malicious").exists())

    def test_upload_material_rejects_oversized_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        oversized = SimpleUploadedFile(
            "huge.jpg", b"\xff\xd8\xff" + b"0" * (6 * 1024 * 1024),  # 6MB, JPEG-ish header
            content_type="image/jpeg",
        )
        response = self.client.post(
            reverse("dashboard:teacher_materials"),
            {
                "class_subject_id": self.class_subject.pk,
                "material_type": CourseMaterial.MaterialType.IMAGE,
                "title": "Too Big", "file": oversized,
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CourseMaterial.objects.filter(title="Too Big").exists())

    def test_submit_assignment_with_file_is_validated(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        assignment = Assignment.objects.create(
            class_subject=self.class_subject, term=self.term, title="Essay Upload",
            deadline=datetime.datetime(2026, 3, 1, tzinfo=datetime.timezone.utc),
        )
        # setUp() logs in as the teacher; switch to the student for this
        # student-facing submission action.
        self.client.logout()
        self.client.login(username=self.student.user.username, password="pass12345")
        essay = SimpleUploadedFile(
            "essay.pdf", b"%PDF-1.4\n" + b"0" * 100, content_type="application/pdf",
        )
        response = self.client.post(
            reverse("dashboard:student_submit_assignment", args=[assignment.pk]),
            {"submitted_file": essay},
        )
        self.assertEqual(response.status_code, 302)
        submission = AssignmentSubmission.objects.get(assignment=assignment, student=self.student)
        self.assertNotIn("essay", submission.submitted_file.name)

    def test_post_course_announcement_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:teacher_announcements", args=[self.class_subject.pk]),
            {"title": "Test postponed", "body": "Moved to next week."},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Discussion.objects.filter(
                class_subject=self.class_subject, thread_type=Discussion.ThreadType.ANNOUNCEMENT,
                title="Test postponed",
            ).exists()
        )

    def test_enter_marks_via_dashboard(self):
        structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term, name="Standard"
        )
        atype = AssessmentType.objects.create(school=self.school, name="CAT", code="CAT1")
        component = AssessmentComponent.objects.create(
            structure=structure, assessment_type=atype,
            weight_percentage=Decimal("100"), max_marks=Decimal("100"),
        )
        assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=component,
            title="CAT 1",
        )
        response = self.client.post(
            reverse("dashboard:teacher_marks_entry", args=[assessment.pk]),
            {f"mark_{self.student.pk}": "78"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AssessmentMark.objects.filter(
                assessment=assessment, student=self.student, marks_obtained=Decimal("78"),
            ).exists()
        )

    def test_teacher_cannot_approve_own_submitted_assessment_via_dashboard(self):
        """Spec §9: 'Teachers must not be able to approve their own final
        results.' Enforced by transition_assessment_workflow() (Phase 8);
        this confirms it still holds when reached through the dashboard
        submit-for-review action followed by an approval attempt by the
        same user."""
        structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term, name="Standard"
        )
        atype = AssessmentType.objects.create(school=self.school, name="Final", code="FINAL")
        component = AssessmentComponent.objects.create(
            structure=structure, assessment_type=atype,
            weight_percentage=Decimal("100"), max_marks=Decimal("100"),
        )
        assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=component,
            title="Final Exam",
        )
        self.client.post(
            reverse("dashboard:teacher_marks_entry", args=[assessment.pk]),
            {"action": "submit_for_review"},
        )
        assessment.refresh_from_db()
        self.assertEqual(assessment.workflow_status, Assessment.WorkflowStatus.SUBMITTED)

        reviewer = User.objects.create_user(
            username="reviewer1", password="pass12345", role=User.Role.CLASS_TEACHER
        )
        transition_assessment_workflow(
            assessment=assessment, to_status=Assessment.WorkflowStatus.REVIEWED,
            actor=reviewer,
        )
        verifier = User.objects.create_user(
            username="verifier1", password="pass12345", role=User.Role.ACADEMIC_ADMIN
        )
        transition_assessment_workflow(
            assessment=assessment, to_status=Assessment.WorkflowStatus.VERIFIED,
            actor=verifier,
        )

        with self.assertRaises(PermissionError):
            transition_assessment_workflow(
                assessment=assessment, to_status=Assessment.WorkflowStatus.APPROVED,
                actor=self.teacher_user,
            )

    def test_non_teacher_role_cannot_access_teacher_dashboard(self):
        self.client.logout()
        parent_user = User.objects.create_user(
            username="notateacher", password="pass12345", role=User.Role.PARENT
        )
        self.client.login(username="notateacher", password="pass12345")
        response = self.client.get(reverse("dashboard:teacher_dashboard"))
        self.assertEqual(response.status_code, 403)


class FinanceAdminDashboardTests(TestCase):
    """Phase 19: Finance Admin dashboard reuses Phase 12's already-tested
    generate_invoice_for_student()/record_payment()/decide_refund() —
    these tests focus on the dashboard wiring (routes, ownership scoping,
    role gating) and spec §23's academic/financial separation (no page
    here should reference AssessmentMark/grades)."""

    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
        )
        self.tuition = FeeCategory.objects.create(
            school=self.school, name="Tuition", code="TUITION"
        )
        self.structure = FeeStructure.objects.create(
            school=self.school, academic_year=self.academic_year, term=self.term,
            name="Term 1 Fees",
        )
        FeeStructureItem.objects.create(
            structure=self.structure, category=self.tuition, amount=Decimal("50000")
        )
        student_user = User.objects.create_user(
            username="financedashstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM990",
            admission_date=datetime.date(2026, 1, 10),
        )
        self.finance_user = User.objects.create_user(
            username="financedashadmin", password="pass12345", role=User.Role.FINANCE_ADMIN
        )
        self.client.login(username="financedashadmin", password="pass12345")

    def test_overview_page_loads_with_zero_state(self):
        response = self.client.get(reverse("dashboard:finance_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_router_sends_finance_admin_to_finance_dashboard(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:finance_dashboard"))

    def test_accountant_role_also_routes_to_finance_dashboard(self):
        self.client.logout()
        accountant = User.objects.create_user(
            username="dashaccountant", password="pass12345", role=User.Role.ACCOUNTANT
        )
        self.client.login(username="dashaccountant", password="pass12345")
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:finance_dashboard"))

    def test_non_finance_role_cannot_access_finance_dashboard(self):
        self.client.logout()
        teacher = User.objects.create_user(
            username="notafinanceperson", password="pass12345", role=User.Role.TEACHER
        )
        self.client.login(username="notafinanceperson", password="pass12345")
        response = self.client.get(reverse("dashboard:finance_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_generate_invoice_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:finance_invoices"),
            {
                "student_id": self.student.pk, "fee_structure_id": self.structure.pk,
                "due_date": "2026-02-01",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Invoice.objects.filter(student=self.student).exists())
        invoice = Invoice.objects.get(student=self.student)
        self.assertEqual(invoice.total_amount, Decimal("50000"))

    def test_invoices_list_page_never_renders_grade_data(self):
        """Spec §23: Finance Admin gets fees/payments/invoices only —
        confirms the invoices page response contains no reference to
        grades/marks/assessments in its rendered output."""
        response = self.client.get(reverse("dashboard:finance_invoices"))
        content = response.content.decode()
        for forbidden_term in ["AssessmentMark", "grade_point", "weighted_total"]:
            self.assertNotIn(forbidden_term, content)

    def test_record_payment_via_dashboard(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_user, due_date=datetime.date(2026, 2, 1),
        )
        response = self.client.post(
            reverse("dashboard:finance_invoice_detail", args=[invoice.pk]),
            {
                "amount": "20000", "payment_method": Payment.Method.CASH,
                "payment_date": "2026-01-15T10:00",
            },
        )
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARTIALLY_PAID)

    def test_overpayment_via_dashboard_is_rejected_not_500(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_user, due_date=datetime.date(2026, 2, 1),
        )
        response = self.client.post(
            reverse("dashboard:finance_invoice_detail", args=[invoice.pk]),
            {
                "amount": "999999", "payment_method": Payment.Method.CASH,
                "payment_date": "2026-01-15T10:00",
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_approve_refund_via_dashboard(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_user, due_date=datetime.date(2026, 2, 1),
        )
        payment = record_payment(
            invoice=invoice, amount=Decimal("50000"), payment_method=Payment.Method.CASH,
            payment_date=datetime.datetime(2026, 1, 15, tzinfo=datetime.timezone.utc),
            received_by=self.finance_user,
        )
        refund = Refund.objects.create(
            payment=payment, amount=Decimal("5000"), reason="Overcharge",
            requested_by=self.finance_user,
        )
        response = self.client.post(
            reverse("dashboard:finance_refunds"),
            {"refund_id": refund.pk, "action": "approve"},
        )
        self.assertEqual(response.status_code, 302)
        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.COMPLETED)

    def test_invoice_detail_shows_correct_student_only(self):
        invoice = generate_invoice_for_student(
            student=self.student, fee_structure=self.structure,
            academic_year=self.academic_year, term=self.term,
            issued_by=self.finance_user, due_date=datetime.date(2026, 2, 1),
        )
        response = self.client.get(
            reverse("dashboard:finance_invoice_detail", args=[invoice.pk])
        )
        self.assertContains(response, self.student.admission_number)


class StaffAdminDashboardTests(TestCase):
    """Phase 20: Staff Admin dashboard. Covers the two real bugs found
    during review — missing `reactivate_staff` import (would 500 on any
    real reactivate action) and the missing self-service leave-submission
    entry point (spec's workflow literally starts with 'Staff submits
    leave request', which had no way to happen before this phase)."""

    def setUp(self):
        self.school = School.objects.create(name="Riverside High", code="RVH")
        self.department = Department.objects.create(
            school=self.school, name="Mathematics Dept", code="MATHDEPT"
        )
        self.staff_admin_user = User.objects.create_user(
            username="dashstaffadmin", password="pass12345", role=User.Role.STAFF_ADMIN
        )
        teacher_user = User.objects.create_user(
            username="dashstaffmember", password="pass12345", role=User.Role.TEACHER,
            first_name="Sam", last_name="Otieno",
        )
        self.staff = Staff.objects.create(
            user=teacher_user, school=self.school, staff_id="STF700",
            department=self.department, job_title="Maths Teacher",
            date_hired=datetime.date(2024, 1, 1),
        )
        self.client.login(username="dashstaffadmin", password="pass12345")

    def test_overview_page_loads(self):
        response = self.client.get(reverse("dashboard:staff_admin_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_router_sends_staff_admin_to_staff_admin_dashboard(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:staff_admin_dashboard"))

    def test_non_staff_admin_cannot_access_staff_admin_dashboard(self):
        self.client.logout()
        teacher = User.objects.create_user(
            username="notastaffadmin", password="pass12345", role=User.Role.TEACHER
        )
        self.client.login(username="notastaffadmin", password="pass12345")
        response = self.client.get(reverse("dashboard:staff_admin_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_staff_list_shows_created_staff(self):
        response = self.client.get(reverse("dashboard:staff_admin_staff_list"))
        self.assertContains(response, "STF700")

    def test_create_staff_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:staff_admin_staff_create"),
            {
                "username": "newteacher1", "first_name": "Amy", "last_name": "Kim",
                "staff_id": "STF701", "job_title": "English Teacher",
                "department_id": self.department.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Staff.objects.filter(staff_id="STF701").exists())
        new_staff = Staff.objects.get(staff_id="STF701")
        self.assertTrue(new_staff.user.is_active)

    def test_deactivate_staff_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:staff_admin_staff_detail", args=[self.staff.pk]),
            {"action": "deactivate", "reason": "Resigned"},
        )
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_active)
        self.assertFalse(self.staff.user.is_active)
        self.assertEqual(self.staff.employment_status, Staff.EmploymentStatus.TERMINATED)

    def test_reactivate_staff_via_dashboard_does_not_500(self):
        """Regression test: reactivate_staff was called in views.py but
        never imported — this action would have raised NameError (500)
        on every real request before the fix."""
        deactivate_staff(staff=self.staff, deactivated_by=self.staff_admin_user)
        response = self.client.post(
            reverse("dashboard:staff_admin_staff_detail", args=[self.staff.pk]),
            {"action": "reactivate"},
        )
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertTrue(self.staff.is_active)
        self.assertTrue(self.staff.user.is_active)

    def test_edit_staff_profile_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:staff_admin_staff_detail", args=[self.staff.pk]),
            {
                "job_title": "Head of Mathematics",
                "emergency_contact_name": "Jane Otieno",
                "emergency_contact_phone": "+254700000099",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.staff.refresh_from_db()
        self.assertEqual(self.staff.job_title, "Head of Mathematics")
        self.assertEqual(self.staff.emergency_contact_name, "Jane Otieno")

    def test_mark_staff_attendance_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:staff_admin_attendance"),
            {"date": "2026-02-02", f"status_{self.staff.pk}": "PRESENT"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            StaffAttendanceRecord.objects.filter(
                staff=self.staff, date="2026-02-02", status="PRESENT",
            ).exists()
        )

    def test_leave_request_full_workflow_via_dashboard(self):
        """Spec §6: 'Staff submits leave request -> Staff Admin reviews
        -> Staff Admin approves/rejects -> System records decision ->
        User receives notification.' Exercised end-to-end through the
        actual views, not just the service layer."""
        self.client.logout()
        self.client.login(username="dashstaffmember", password="pass12345")
        response = self.client.post(
            reverse("dashboard:my_leave_requests"),
            {
                "leave_type": LeaveRequest.LeaveType.ANNUAL,
                "start_date": "2026-03-01", "end_date": "2026-03-05",
                "reason": "Family trip",
            },
        )
        self.assertEqual(response.status_code, 302)
        leave_request = LeaveRequest.objects.get(staff=self.staff)
        self.assertEqual(leave_request.status, LeaveRequest.Status.PENDING)

        self.client.logout()
        self.client.login(username="dashstaffadmin", password="pass12345")
        response = self.client.post(
            reverse("dashboard:staff_admin_leave_requests"),
            {"leave_request_id": leave_request.pk, "action": "approve"},
        )
        self.assertEqual(response.status_code, 302)
        leave_request.refresh_from_db()
        self.assertEqual(leave_request.status, LeaveRequest.Status.APPROVED)

        from smsApp.models import Notification
        self.assertTrue(
            Notification.objects.filter(
                recipient=self.staff.user, title__icontains="Leave Request",
            ).exists()
        )

    def test_cannot_decide_same_leave_request_twice_via_dashboard(self):
        leave_request = submit_leave_request(
            staff=self.staff, leave_type=LeaveRequest.LeaveType.SICK,
            start_date=datetime.date(2026, 3, 1), end_date=datetime.date(2026, 3, 2),
        )
        decide_leave_request(
            leave_request=leave_request, approve=True, decided_by=self.staff_admin_user,
        )
        response = self.client.post(
            reverse("dashboard:staff_admin_leave_requests"),
            {"leave_request_id": leave_request.pk, "action": "approve"},
        )
        self.assertEqual(response.status_code, 403)

    def test_workload_page_reflects_teaching_assignment(self):
        program = Program.objects.create(school=self.school, name="8-4-4", code="844")
        class_group = Class.objects.create(school=self.school, program=program, name="Grade 10")
        subject = Subject.objects.create(school=self.school, code="MATH", name="Mathematics")
        class_subject = ClassSubject.objects.create(class_group=class_group, subject=subject)
        academic_year = AcademicYear.objects.create(
            school=self.school, name="2026/2027",
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31),
        )
        term = Term.objects.create(
            academic_year=academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
            is_current=True,
        )
        TeachingAssignment.objects.create(
            class_subject=class_subject, teacher=self.staff, term=term
        )
        response = self.client.get(reverse("dashboard:staff_admin_workload"))
        self.assertEqual(response.status_code, 200)
        row = next(
            r for r in response.context["workload_rows"] if r["staff"].pk == self.staff.pk
        )
        self.assertEqual(row["workload"]["assigned_classes"], 1)

    def test_my_leave_requests_page_accessible_to_any_staff_role(self):
        """The self-service view isn't gated to a specific staff role —
        confirms a Librarian (not Teacher/Staff Admin) can still submit
        their own leave request."""
        librarian_user = User.objects.create_user(
            username="dashlibrarian", password="pass12345", role=User.Role.LIBRARIAN
        )
        Staff.objects.create(
            user=librarian_user, school=self.school, staff_id="STF702",
            job_title="Librarian", date_hired=datetime.date(2024, 1, 1),
        )
        self.client.logout()
        self.client.login(username="dashlibrarian", password="pass12345")
        response = self.client.get(reverse("dashboard:my_leave_requests"))
        self.assertEqual(response.status_code, 200)

    def test_non_staff_user_gets_404_on_my_leave_requests(self):
        self.client.logout()
        parent_user = User.objects.create_user(
            username="dashparentnotstaff", password="pass12345", role=User.Role.PARENT
        )
        self.client.login(username="dashparentnotstaff", password="pass12345")
        response = self.client.get(reverse("dashboard:my_leave_requests"))
        self.assertEqual(response.status_code, 404)


class AcademicAdminDashboardTests(TestCase):
    """Phase 21: Academic Admin dashboard. Every view reuses already-
    tested service functions from Phases 6/7/8 (correct_attendance_record,
    transition_assessment_workflow) rather than reimplementing logic —
    tests focus on the dashboard wiring, ownership scoping, role gating,
    and spec §7's 'without accessing confidential financial information'
    constraint."""

    def setUp(self):
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
            is_current=True,
        )
        self.term = Term.objects.create(
            academic_year=self.academic_year, name="Term 1", term_number=1,
            start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 4, 1),
            is_current=True,
        )
        self.academic_admin_user = User.objects.create_user(
            username="dashacademicadmin", password="pass12345", role=User.Role.ACADEMIC_ADMIN
        )
        student_user = User.objects.create_user(
            username="dashacademicstudent", password="pass12345", role=User.Role.STUDENT
        )
        self.student = Student.objects.create(
            user=student_user, school=self.school, admission_number="ADM950",
            admission_date=datetime.date(2026, 1, 10), current_class=self.class_group,
        )
        self.client.login(username="dashacademicadmin", password="pass12345")

    def test_overview_page_loads(self):
        response = self.client.get(reverse("dashboard:academic_admin_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_dashboard_router_sends_academic_admin_to_academic_admin_dashboard(self):
        response = self.client.get(reverse("dashboard:home"))
        self.assertRedirects(response, reverse("dashboard:academic_admin_dashboard"))

    def test_non_academic_admin_cannot_access_dashboard(self):
        self.client.logout()
        teacher = User.objects.create_user(
            username="notaacademicadmin", password="pass12345", role=User.Role.TEACHER
        )
        self.client.login(username="notaacademicadmin", password="pass12345")
        response = self.client.get(reverse("dashboard:academic_admin_dashboard"))
        self.assertEqual(response.status_code, 403)

    def test_overview_never_renders_financial_data(self):
        """Spec §7: 'Academic Admin must manage academic operations
        without accessing confidential financial information.'"""
        response = self.client.get(reverse("dashboard:academic_admin_dashboard"))
        content = response.content.decode()
        for forbidden_term in ["Invoice", "invoice_number", "total_billed", "outstanding_balance"]:
            self.assertNotIn(forbidden_term, content)

    def test_students_list_shows_registered_student(self):
        response = self.client.get(reverse("dashboard:academic_admin_students"))
        self.assertContains(response, "ADM950")

    def test_register_student_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:academic_admin_students"),
            {
                "username": "newstudent1", "first_name": "Amy", "last_name": "Kim",
                "admission_number": "ADM951", "admission_date": "2026-01-10",
                "current_class_id": self.class_group.pk,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Student.objects.filter(admission_number="ADM951").exists())

    def test_student_detail_shows_correct_student(self):
        response = self.client.get(
            reverse("dashboard:academic_admin_student_detail", args=[self.student.pk])
        )
        self.assertContains(response, "ADM950")

    def test_change_student_status_via_dashboard(self):
        response = self.client.post(
            reverse("dashboard:academic_admin_student_detail", args=[self.student.pk]),
            {"status": Student.Status.SUSPENDED, "reason": "Disciplinary review"},
        )
        self.assertEqual(response.status_code, 302)
        self.student.refresh_from_db()
        self.assertEqual(self.student.status, Student.Status.SUSPENDED)

    def test_student_detail_shows_guardian_and_enrollment_history(self):
        guardian = Guardian.objects.create(
            school=self.school, first_name="Jane", last_name="Doe",
            relationship="Mother", phone_number="+254700000000",
        )
        StudentGuardian.objects.create(
            student=self.student, guardian=guardian, is_primary_contact=True,
        )
        Enrollment.objects.create(
            student=self.student, class_subject=self.class_subject,
            academic_year=self.academic_year,
        )
        response = self.client.get(
            reverse("dashboard:academic_admin_student_detail", args=[self.student.pk])
        )
        self.assertContains(response, "Jane")
        self.assertContains(response, "Mathematics")

    def test_result_approval_queue_shows_submitted_assessment(self):
        structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term, name="Standard"
        )
        atype = AssessmentType.objects.create(school=self.school, name="Final", code="FINAL")
        component = AssessmentComponent.objects.create(
            structure=structure, assessment_type=atype,
            weight_percentage=Decimal("100"), max_marks=Decimal("100"),
        )
        assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=component,
            title="Final Exam", workflow_status=Assessment.WorkflowStatus.SUBMITTED,
        )
        response = self.client.get(reverse("dashboard:academic_admin_results_approval"))
        self.assertContains(response, "Final Exam")
        self.assertEqual(list(response.context["assessments"]), [assessment])

    def test_advance_assessment_through_workflow_via_dashboard(self):
        structure = AssessmentStructure.objects.create(
            school=self.school, term=self.term, name="Standard"
        )
        atype = AssessmentType.objects.create(school=self.school, name="Final", code="FINAL2")
        component = AssessmentComponent.objects.create(
            structure=structure, assessment_type=atype,
            weight_percentage=Decimal("100"), max_marks=Decimal("100"),
        )
        assessment = Assessment.objects.create(
            class_subject=self.class_subject, term=self.term, component=component,
            title="CAT 1", workflow_status=Assessment.WorkflowStatus.SUBMITTED,
        )
        response = self.client.post(
            reverse("dashboard:academic_admin_results_approval"),
            {"assessment_id": assessment.pk, "action": "review"},
        )
        self.assertEqual(response.status_code, 302)
        assessment.refresh_from_db()
        self.assertEqual(assessment.workflow_status, Assessment.WorkflowStatus.REVIEWED)

    def test_correct_attendance_via_dashboard(self):
        session = AttendanceSession.objects.create(
            class_subject=self.class_subject, term=self.term, date=datetime.date(2026, 2, 3),
        )
        record = AttendanceRecord.objects.create(
            session=session, student=self.student, status="ABSENT",
        )
        response = self.client.post(
            reverse("dashboard:academic_admin_attendance_correction"),
            {"record_id": record.pk, "status": "PRESENT", "notes": "Was marked absent by mistake"},
        )
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, "PRESENT")

        audit_entry = AuditLog.objects.filter(
            target_model="AttendanceRecord", target_object_id=str(record.pk)
        ).first()
        self.assertIsNotNone(audit_entry)

    def test_invalid_student_status_rejected(self):
        response = self.client.post(
            reverse("dashboard:academic_admin_student_detail", args=[self.student.pk]),
            {"status": "NOT_A_REAL_STATUS"},
        )
        self.assertEqual(response.status_code, 403)


class UploadValidatorTests(TestCase):
    """Phase 24: spec §27 'File validation', 'Upload size restrictions'.
    Confirms validators sniff actual file content via libmagic rather
    than trusting the filename/extension or a spoofed Content-Type."""

    # A real, complete, minimal valid 1x1 PNG.
    REAL_PNG_BYTES = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000031e97a"
        "c00000000a49444154789c6300010000050001a5a0f5980000000049454e44ae"
        "426082"
    )
    # A real, minimal valid PDF.
    REAL_PDF_BYTES = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    )

    def test_real_image_passes_content_validation(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("photo.png", self.REAL_PNG_BYTES, content_type="image/png")
        validate_image_content(f)  # must not raise

    def test_renamed_non_image_fails_content_validation_even_with_image_extension(self):
        """The core spoofing scenario spec §27 is guarding against: a
        file that is NOT actually an image, renamed to look like one."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        fake_image = SimpleUploadedFile(
            "totally_a_photo.jpg", b"this is definitely not image data, just text",
            content_type="image/jpeg",  # the spoofed header alone must not be trusted
        )
        with self.assertRaises(ValidationError):
            validate_image_content(fake_image)

    def test_real_pdf_passes_pdf_validation(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("report.pdf", self.REAL_PDF_BYTES, content_type="application/pdf")
        validate_pdf_content(f)  # must not raise

    def test_image_rejected_by_pdf_only_validator(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        f = SimpleUploadedFile("photo.png", self.REAL_PNG_BYTES, content_type="image/png")
        with self.assertRaises(ValidationError):
            validate_pdf_content(f)

    def test_document_validator_accepts_both_images_and_pdfs(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        image = SimpleUploadedFile("photo.png", self.REAL_PNG_BYTES, content_type="image/png")
        pdf = SimpleUploadedFile("doc.pdf", self.REAL_PDF_BYTES, content_type="application/pdf")
        validate_document_content(image)  # must not raise
        validate_document_content(pdf)  # must not raise

    def test_file_size_validator_rejects_oversized_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        oversized = SimpleUploadedFile("big.png", b"x" * (6 * 1024 * 1024))  # 6 MB
        validator = validate_file_size(5)  # 5 MB limit
        with self.assertRaises(ValidationError):
            validator(oversized)

    def test_file_size_validator_accepts_file_within_limit(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        small_file = SimpleUploadedFile("small.png", b"x" * 1024)  # 1 KB
        validator = validate_file_size(5)
        validator(small_file)  # must not raise

    def test_school_logo_field_actually_enforces_validators_via_full_clean(self):
        """End-to-end: confirms the validators are actually wired onto
        the model field (not just defined and unused) by going through
        Django's real validation path, full_clean(), the same path a
        ModelForm-based upload view exercises."""
        from django.core.files.uploadedfile import SimpleUploadedFile
        school = School(
            name="Test School", code="VALTEST",
            logo=SimpleUploadedFile(
                "fake.png", b"not a real image", content_type="image/png"
            ),
        )
        with self.assertRaises(ValidationError):
            school.full_clean()

    def test_school_logo_field_accepts_real_image_via_full_clean(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        school = School(
            name="Test School 2", code="VALTEST2",
            logo=SimpleUploadedFile(
                "real.png", self.REAL_PNG_BYTES, content_type="image/png"
            ),
        )
        school.full_clean()  # must not raise
