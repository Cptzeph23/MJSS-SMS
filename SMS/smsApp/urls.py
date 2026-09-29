# Absolute path: SMS/smsApp/urls.py
from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("account-locked/", views.AccountLockedView.as_view(), name="account_locked"),
    path("", views.DashboardRouterView.as_view(), name="home"),
    path("coming-soon/", views.ComingSoonView.as_view(), name="coming_soon"),
    path("super-admin/", views.SuperAdminDashboardView.as_view(), name="super_admin"),
    path("super-admin/users/", views.SuperAdminUsersView.as_view(), name="super_admin_users"),
    path("super-admin/school-config/", views.SuperAdminSchoolConfigView.as_view(), name="super_admin_school_config"),
    path("super-admin/audit-logs/", views.SuperAdminAuditLogsView.as_view(), name="super_admin_audit_logs"),
    path(
        "reports/<int:report_card_id>/html/",
        views.ReportCardHTMLView.as_view(), name="report_card_html",
    ),
    path(
        "reports/<int:report_card_id>/pdf/",
        views.ReportCardPDFView.as_view(), name="report_card_pdf",
    ),
    path("reports/batch-generate/", views.BatchReportGenerateView.as_view(), name="report_batch_generate"),
    path(
        "transcripts/<int:student_id>/download/",
        views.TranscriptGenerateAndDownloadView.as_view(), name="transcript_download",
    ),
    path(
        "transcripts/verify/<uuid:verification_code>/",
        views.TranscriptVerifyView.as_view(), name="transcript_verify",
    ),
    path("student/", views.StudentDashboardView.as_view(), name="student_dashboard"),
    path("student/academic/", views.StudentAcademicView.as_view(), name="student_academic"),
    path("student/lms/", views.StudentLMSView.as_view(), name="student_lms"),
    path(
        "student/lms/assignments/<int:assignment_id>/submit/",
        views.StudentSubmitAssignmentView.as_view(), name="student_submit_assignment",
    ),
    path(
        "student/lms/quizzes/<int:quiz_id>/attempt/",
        views.StudentQuizAttemptView.as_view(), name="student_quiz_attempt",
    ),
    path("student/finance/", views.StudentFinanceView.as_view(), name="student_finance"),
    path(
        "student/communication/", views.StudentCommunicationView.as_view(),
        name="student_communication",
    ),
    path(
        "student/notifications/<int:notification_id>/read/",
        views.StudentMarkNotificationReadView.as_view(), name="student_mark_notification_read",
    ),
    path("notifications/<int:notification_id>/open/", views.NotificationOpenView.as_view(), name="notification_open"),
    path("notifications/feed/", views.NotificationFeedView.as_view(), name="notification_feed"),
    path("parent/", views.ParentDashboardView.as_view(), name="parent_dashboard"),
    path(
        "parent/children/<int:student_id>/academic/",
        views.ParentChildAcademicView.as_view(), name="parent_child_academic",
    ),
    path(
        "parent/children/<int:student_id>/finance/",
        views.ParentChildFinanceView.as_view(), name="parent_child_finance",
    ),
    path(
        "parent/communication/", views.ParentCommunicationView.as_view(),
        name="parent_communication",
    ),
    path(
        "parent/notifications/<int:notification_id>/read/",
        views.ParentMarkNotificationReadView.as_view(), name="parent_mark_notification_read",
    ),
    path("teacher/", views.TeacherDashboardView.as_view(), name="teacher_dashboard"),
    path("teacher/classes/", views.TeacherClassesView.as_view(), name="teacher_classes"),
    path(
        "teacher/classes/<int:class_subject_id>/roster/",
        views.TeacherClassRosterView.as_view(), name="teacher_class_roster",
    ),
    path("teacher/timetable/", views.TeacherTimetableView.as_view(), name="teacher_timetable"),
    path(
        "teacher/classes/<int:class_subject_id>/attendance/",
        views.TeacherAttendanceView.as_view(), name="teacher_attendance",
    ),
    path("teacher/assignments/", views.TeacherAssignmentsView.as_view(), name="teacher_assignments"),
    path(
        "teacher/assignments/<int:assignment_id>/submissions/",
        views.TeacherAssignmentSubmissionsView.as_view(), name="teacher_assignment_submissions",
    ),
    path("teacher/materials/", views.TeacherMaterialsView.as_view(), name="teacher_materials"),
    path(
        "teacher/classes/<int:class_subject_id>/announcements/",
        views.TeacherAnnouncementsView.as_view(), name="teacher_announcements",
    ),
    path("teacher/assessments/", views.TeacherAssessmentsView.as_view(), name="teacher_assessments"),
    path(
        "teacher/assessments/<int:quiz_id>/attempts/",
        views.TeacherQuizAttemptsView.as_view(), name="teacher_quiz_attempts",
    ),
    path(
        "teacher/assessments/<int:assessment_id>/marks/",
        views.TeacherMarksEntryView.as_view(), name="teacher_marks_entry",
    ),
    path(
        "teacher/communication/", views.TeacherCommunicationView.as_view(),
        name="teacher_communication",
    ),
    path(
        "teacher/notifications/<int:notification_id>/read/",
        views.TeacherMarkNotificationReadView.as_view(), name="teacher_mark_notification_read",
    ),
    path("finance/", views.FinanceAdminDashboardView.as_view(), name="finance_dashboard"),
    path("finance/invoices/", views.FinanceAdminInvoicesView.as_view(), name="finance_invoices"),
    path(
        "finance/invoices/<int:invoice_id>/",
        views.FinanceAdminInvoiceDetailView.as_view(), name="finance_invoice_detail",
    ),
    path("finance/refunds/", views.FinanceAdminRefundsView.as_view(), name="finance_refunds"),
    path("staff-admin/", views.StaffAdminDashboardView.as_view(), name="staff_admin_dashboard"),
    path("staff-admin/staff/", views.StaffAdminStaffListView.as_view(), name="staff_admin_staff_list"),
    path("staff-admin/staff/create/", views.StaffAdminStaffCreateView.as_view(), name="staff_admin_staff_create"),
    path(
        "staff-admin/staff/<int:staff_id>/",
        views.StaffAdminStaffDetailView.as_view(), name="staff_admin_staff_detail",
    ),
    path("staff-admin/attendance/", views.StaffAdminAttendanceView.as_view(), name="staff_admin_attendance"),
    path("staff-admin/leave/", views.StaffAdminLeaveRequestsView.as_view(), name="staff_admin_leave_requests"),
    path("staff-admin/workload/", views.StaffAdminWorkloadView.as_view(), name="staff_admin_workload"),
    path("my-leave-requests/", views.MyLeaveRequestsView.as_view(), name="my_leave_requests"),
    path("academic-admin/", views.AcademicAdminDashboardView.as_view(), name="academic_admin_dashboard"),
    path("academic-admin/students/", views.AcademicAdminStudentsView.as_view(), name="academic_admin_students"),
    path(
        "academic-admin/students/<int:student_id>/",
        views.AcademicAdminStudentDetailView.as_view(), name="academic_admin_student_detail",
    ),
    path(
        "academic-admin/results/", views.AcademicAdminResultsApprovalView.as_view(),
        name="academic_admin_results_approval",
    ),
    path(
        "academic-admin/attendance/", views.AcademicAdminAttendanceCorrectionView.as_view(),
        name="academic_admin_attendance_correction",
    ),
]
