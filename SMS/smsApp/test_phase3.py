from datetime import date
from unittest.mock import patch

from django.test import TestCase

from .models import (
    AcademicYear, AttendanceRecord, Class, ClassSubject, Enrollment, Guardian,
    NotificationDelivery, NotificationPreference, School, Staff, Student,
    StudentGuardian, Subject, TeachingAssignment, Term, User,
)
from .services import mark_attendance


class AttendanceSMSPhase3Tests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Attendance School", code="ATT")
        program = __import__("smsApp.models", fromlist=["Program"]).Program.objects.create(
            school=self.school, name="8-4-4", code="844"
        )
        self.class_group = Class.objects.create(
            school=self.school, program=program, name="Grade 5"
        )
        subject = Subject.objects.create(
            school=self.school, code="MATH", name="Mathematics"
        )
        self.class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=subject
        )
        year = AcademicYear.objects.create(
            school=self.school, name="2026", start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31)
        )
        self.term = Term.objects.create(
            academic_year=year, name="Term 1", term_number=1,
            start_date=date(2026, 1, 1), end_date=date(2026, 4, 1)
        )
        teacher_user = User.objects.create_user(
            username="attteacher", password="pass12345", role=User.Role.TEACHER
        )
        self.teacher = Staff.objects.create(
            user=teacher_user, school=self.school, staff_id="ATT-T",
            job_title="Teacher", date_hired=date(2024, 1, 1)
        )
        TeachingAssignment.objects.create(
            class_subject=self.class_subject, teacher=self.teacher, term=self.term
        )
        parent_user = User.objects.create_user(
            username="attparent", password="pass12345", role=User.Role.PARENT
        )
        self.guardian = Guardian.objects.create(
            user=parent_user, school=self.school, first_name="Att",
            last_name="Parent", relationship="Parent", phone_number="+254700000002"
        )
        self.students = []
        for index in range(4):
            user = User.objects.create_user(
                username=f"attstudent{index}", password="pass12345",
                role=User.Role.STUDENT, first_name=f"Student {index}"
            )
            student = Student.objects.create(
                user=user, school=self.school, admission_number=f"ATT-{index}",
                admission_date=date(2026, 1, 1)
            )
            Enrollment.objects.create(
                student=student, class_subject=self.class_subject,
                academic_year=year
            )
            StudentGuardian.objects.create(student=student, guardian=self.guardian)
            self.students.append(student)
        NotificationPreference.objects.create(
            user=parent_user, sms_enabled=True
        )

    def test_all_attendance_statuses_are_saved_and_sms_failures_do_not_rollback(self):
        statuses = {
            self.students[0].pk: {"status": AttendanceRecord.Status.PRESENT},
            self.students[1].pk: {"status": AttendanceRecord.Status.ABSENT},
            self.students[2].pk: {"status": AttendanceRecord.Status.LATE},
            self.students[3].pk: {"status": AttendanceRecord.Status.EXCUSED},
        }
        with patch.dict("os.environ", {"SMS_PROVIDER": "http"}, clear=False):
            session = mark_attendance(
                class_subject=self.class_subject, term=self.term,
                date=date(2026, 2, 2), taken_by=self.teacher, records=statuses
            )
        self.assertEqual(session.records.count(), 4)
        self.assertEqual(
            set(session.records.values_list("status", flat=True)),
            {"PRESENT", "ABSENT", "LATE", "EXCUSED"},
        )
        sms_deliveries = NotificationDelivery.objects.filter(
            channel=NotificationDelivery.Channel.SMS
        )
        self.assertEqual(sms_deliveries.count(), 3)
        self.assertTrue(all(d.status == NotificationDelivery.Status.FAILED for d in sms_deliveries))
        self.assertTrue(all("not configured" in d.error_message for d in sms_deliveries))

    def test_sms_provider_success_records_provider_reference(self):
        from smsApp.sms import SMSResponse
        with patch("smsApp.sms.send_sms", return_value=SMSResponse("msg-123")):
            from .services import send_notification
            notification = send_notification(
                recipient=self.guardian.user,
                notification_type="ATTENDANCE",
                title="Attendance", body="Absent", channels=["SMS"]
            )
        delivery = notification.deliveries.get(channel=NotificationDelivery.Channel.SMS)
        self.assertEqual(delivery.status, NotificationDelivery.Status.SENT)
        self.assertEqual(delivery.provider_reference, "msg-123")

    def test_teacher_cannot_mark_unassigned_class(self):
        other_subject = Subject.objects.create(
            school=self.school, code="ENG", name="English"
        )
        other_class_subject = ClassSubject.objects.create(
            class_group=self.class_group, subject=other_subject
        )
        with self.assertRaises(ValueError):
            mark_attendance(
                class_subject=other_class_subject, term=self.term,
                date=date(2026, 2, 3), taken_by=self.teacher,
                records={}
            )
