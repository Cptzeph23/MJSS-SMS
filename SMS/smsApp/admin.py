# Absolute path: SMS/smsApp/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.db.models import Case, DecimalField, F, Sum, When

from .models import (
    AcademicYear,
    Announcement,
    Assessment,
    AssessmentComponent,
    AssessmentMark,
    AssessmentStructure,
    AssessmentType,
    Assignment,
    AssignmentResource,
    AssignmentSubmission,
    AttendanceRecord,
    Author,
    Book,
    BookCategory,
    BookCopy,
    Borrowing,
    AttendanceSession,
    AuditLog,
    Campus,
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
    LibrarySettings,
    LeaveRequest,
    LoginHistory,
    Notification,
    NotificationDelivery,
    NotificationPreference,
    Payment,
    Program,
    Publisher,
    Quiz,
    QuizAnswer,
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
    StaffQualification,
    Stream,
    Student,
    StudentGuardian,
    Subject,
    TeachingAssignment,
    Term,
    TimetableSlot,
    Transcript,
    TranscriptEntry,
    User,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "is_active", "is_locked", "is_staff")
    list_filter = ("role", "is_active", "is_locked", "is_staff")
    search_fields = ("username", "email", "first_name", "last_name")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("SMS Profile", {"fields": ("role", "phone_number", "is_locked")}),
    )


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "created_at")
    search_fields = ("name", "code")


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "is_main", "is_active")
    list_filter = ("school", "is_main", "is_active")
    search_fields = ("name", "code")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "campus", "head", "is_active")
    list_filter = ("school", "campus", "is_active")
    search_fields = ("name", "code")


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "program_type", "is_active")
    list_filter = ("school", "program_type", "is_active")
    search_fields = ("name", "code")


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "start_date", "end_date", "is_current")
    list_filter = ("school", "is_current")


@admin.register(Term)
class TermAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "term_number", "start_date", "end_date", "is_current")
    list_filter = ("academic_year__school", "is_current")


@admin.register(Class)
class ClassAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "school", "campus", "class_teacher", "is_active")
    list_filter = ("school", "program", "campus", "is_active")
    search_fields = ("name",)


@admin.register(Stream)
class StreamAdmin(admin.ModelAdmin):
    list_display = ("name", "class_group", "capacity", "is_active")
    list_filter = ("class_group__school", "is_active")
    search_fields = ("name",)


@admin.register(Guardian)
class GuardianAdmin(admin.ModelAdmin):
    list_display = ("first_name", "last_name", "relationship", "phone_number", "school", "is_active")
    list_filter = ("school", "relationship", "is_active")
    search_fields = ("first_name", "last_name", "phone_number", "email", "national_id")


class StudentGuardianInline(admin.TabularInline):
    model = StudentGuardian
    extra = 1


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "admission_number", "user", "school", "current_class", "current_stream",
        "status", "is_active",
    )
    list_filter = ("school", "status", "current_class", "current_stream", "is_active")
    search_fields = ("admission_number", "user__first_name", "user__last_name", "national_id")
    inlines = [StudentGuardianInline]


class StaffQualificationInline(admin.TabularInline):
    model = StaffQualification
    extra = 1


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = (
        "staff_id", "user", "school", "department", "job_title",
        "employment_status", "employment_type", "is_active",
    )
    list_filter = ("school", "department", "employment_status", "employment_type", "is_active")
    search_fields = ("staff_id", "user__first_name", "user__last_name", "job_title")
    inlines = [StaffQualificationInline]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only in admin — audit rows must never be edited/deleted from
    the UI (spec §37/§38 'never silently change'), only written via
    smsApp.services.log_audit()."""

    list_display = ("created_at", "actor", "action", "target_model", "target_object_id", "ip_address")
    list_filter = ("action", "target_model")
    search_fields = ("actor__username", "target_model", "target_object_id", "description")
    readonly_fields = [f.name for f in AuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LoginHistory)
class LoginHistoryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "was_successful", "ip_address")
    list_filter = ("was_successful",)
    search_fields = ("user__username",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "department", "subject_type", "credit_hours", "is_active")
    list_filter = ("school", "department", "subject_type", "is_active")
    search_fields = ("name", "code")
    filter_horizontal = ("prerequisites",)


@admin.register(ClassSubject)
class ClassSubjectAdmin(admin.ModelAdmin):
    list_display = ("subject", "class_group", "is_active")
    list_filter = ("class_group__school", "is_active")
    search_fields = ("subject__name", "class_group__name")


@admin.register(TeachingAssignment)
class TeachingAssignmentAdmin(admin.ModelAdmin):
    list_display = ("class_subject", "teacher", "term", "is_active")
    list_filter = ("term", "is_active")
    search_fields = ("teacher__staff_id", "class_subject__subject__name")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "class_subject", "academic_year", "status", "enrolled_on")
    list_filter = ("academic_year", "status")
    search_fields = ("student__admission_number", "class_subject__subject__name")


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ("class_subject", "date", "term", "taken_by", "is_locked")
    list_filter = ("term", "is_locked", "class_subject__class_group__school")
    search_fields = ("class_subject__subject__name",)
    inlines = [AttendanceRecordInline]


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    """Direct editing here bypasses the audit trail in
    smsApp.services.correct_attendance_record() — kept available for Super
    Admin emergency fixes, but the in-app correction workflow (Phase 7+)
    should be the normal path so corrections are logged."""

    list_display = ("student", "session", "status", "recorded_by", "updated_at")
    list_filter = ("status", "session__term")
    search_fields = ("student__admission_number",)


@admin.register(AssessmentType)
class AssessmentTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "is_active")
    list_filter = ("school", "is_active")
    search_fields = ("name", "code")


class AssessmentComponentInline(admin.TabularInline):
    model = AssessmentComponent
    extra = 1


@admin.register(AssessmentStructure)
class AssessmentStructureAdmin(admin.ModelAdmin):
    list_display = ("name", "term", "subject", "school", "is_active")
    list_filter = ("school", "term", "is_active")
    search_fields = ("name",)
    inlines = [AssessmentComponentInline]

    def total_weight(self, obj):
        return sum(c.weight_percentage for c in obj.components.all())
    total_weight.short_description = "Total weight %"


class GradeBandInline(admin.TabularInline):
    model = GradeBand
    extra = 1


@admin.register(GradingScheme)
class GradingSchemeAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "is_default", "is_active")
    list_filter = ("school", "is_default", "is_active")
    search_fields = ("name",)
    inlines = [GradeBandInline]


@admin.register(Assessment)
class AssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "title", "class_subject", "term", "component", "date",
        "workflow_status", "is_published",
    )
    list_filter = ("term", "workflow_status", "is_published", "class_subject__class_group__school")
    search_fields = ("title",)
    readonly_fields = (
        "submitted_by", "submitted_at", "reviewed_by", "reviewed_at",
        "verified_by", "verified_at", "approved_by", "approved_at",
        "published_by", "published_at",
    )


@admin.register(AssessmentMark)
class AssessmentMarkAdmin(admin.ModelAdmin):
    list_display = ("student", "assessment", "marks_obtained", "recorded_by", "updated_at")
    list_filter = ("assessment__term",)
    search_fields = ("student__admission_number",)


@admin.register(ResultAmendmentRequest)
class ResultAmendmentRequestAdmin(admin.ModelAdmin):
    """List/review only — approving or rejecting must go through
    smsApp.services.decide_result_amendment() so the mark change and
    audit log stay in sync. Direct admin edits to `status` here would
    silently desync the mark from the request."""

    list_display = (
        "assessment_mark", "original_mark", "proposed_mark", "status",
        "requested_by", "requested_at",
    )
    list_filter = ("status",)
    search_fields = ("assessment_mark__student__admission_number", "reason")
    readonly_fields = (
        "assessment_mark", "original_mark", "proposed_mark", "requested_by",
        "requested_at", "reviewed_by", "reviewed_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(ReportTemplate)
class ReportTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name", "school", "template_key", "show_position", "show_gpa",
        "show_attendance", "is_default", "is_active",
    )
    list_filter = ("school", "template_key", "is_default", "is_active")


@admin.register(ReportCard)
class ReportCardAdmin(admin.ModelAdmin):
    """Generation is via smsApp.services.generate_report_pdf() (called from
    the report views) — editing pdf_file directly here would desync the
    stored file from the underlying assembled data."""

    list_display = (
        "student", "term", "template", "is_finalized", "generated_by", "generated_at",
    )
    list_filter = ("term", "template", "is_finalized")
    search_fields = ("student__admission_number",)
    readonly_fields = ("pdf_file", "generated_by", "generated_at")


class TranscriptEntryInline(admin.TabularInline):
    model = TranscriptEntry
    extra = 0
    readonly_fields = (
        "subject", "subject_name", "academic_year_label", "term_label",
        "score", "grade", "grade_point", "credit_hours",
    )

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Transcript)
class TranscriptAdmin(admin.ModelAdmin):
    """A snapshotted permanent record (see models.Transcript docstring) —
    generated only via smsApp.services.generate_transcript(); nothing here
    is editable, since altering an issued transcript after the fact would
    defeat its verification_code/content_hash tamper-evidence."""

    list_display = (
        "student", "generated_at", "academic_status", "graduation_status",
        "gpa", "cgpa", "verification_code",
    )
    list_filter = ("academic_status", "graduation_status")
    search_fields = ("student__admission_number", "verification_code")
    readonly_fields = [f.name for f in Transcript._meta.fields]
    inlines = [TranscriptEntryInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CourseMaterial)
class CourseMaterialAdmin(admin.ModelAdmin):
    list_display = ("title", "material_type", "class_subject", "term", "is_published", "order")
    list_filter = ("material_type", "term", "is_published")
    search_fields = ("title",)


class AssignmentResourceInline(admin.TabularInline):
    model = AssignmentResource
    extra = 0


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "title", "class_subject", "term", "deadline", "max_marks",
        "allow_resubmission", "is_published",
    )
    list_filter = ("term", "submission_format", "allow_resubmission", "is_published")
    search_fields = ("title",)
    inlines = [AssignmentResourceInline]


@admin.register(AssignmentSubmission)
class AssignmentSubmissionAdmin(admin.ModelAdmin):
    """Grading should go through smsApp.services.grade_assignment_submission()
    so marks changes stay audit-logged — this admin view is for
    oversight/browsing, not the primary grading workflow."""

    list_display = (
        "student", "assignment", "attempt_number", "status", "is_late",
        "marks_obtained", "submitted_at",
    )
    list_filter = ("status", "is_late", "assignment__term")
    search_fields = ("student__admission_number", "assignment__title")


class QuizOptionInline(admin.TabularInline):
    model = QuizOption
    extra = 2


@admin.register(QuizQuestion)
class QuizQuestionAdmin(admin.ModelAdmin):
    list_display = ("question_text", "quiz", "question_type", "marks", "order")
    list_filter = ("quiz", "question_type")
    inlines = [QuizOptionInline]


class QuizQuestionInline(admin.TabularInline):
    model = QuizQuestion
    extra = 1
    show_change_link = True


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("title", "class_subject", "term", "max_attempts", "is_published")
    list_filter = ("term", "is_published")
    search_fields = ("title",)
    inlines = [QuizQuestionInline]


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    """Auto-grading runs via smsApp.services.submit_quiz_attempt(); manual
    short-answer grading via grade_quiz_short_answer() — both keep
    auto_score/manual_score/is_fully_graded in sync. Editing scores
    directly here bypasses that and is for emergency correction only."""

    list_display = (
        "student", "quiz", "attempt_number", "auto_score", "manual_score",
        "is_fully_graded", "submitted_at",
    )
    list_filter = ("quiz", "is_fully_graded")
    search_fields = ("student__admission_number",)


class DiscussionReplyInline(admin.TabularInline):
    model = DiscussionReply
    extra = 0


@admin.register(Discussion)
class DiscussionAdmin(admin.ModelAdmin):
    list_display = ("title", "thread_type", "class_subject", "term", "created_by", "is_pinned", "created_at")
    list_filter = ("thread_type", "term", "is_pinned")
    search_fields = ("title",)
    inlines = [DiscussionReplyInline]


# ---------------------------------------------------------------------
# Finance (Phase 12, spec §19). list_display/search_fields intentionally
# expose only minimal student identity (name, admission number) — never
# join into academic data (grades, assessments) from these registrations,
# per spec 'Do not expose academic grades to Finance Admin'.
# ---------------------------------------------------------------------

@admin.register(FeeCategory)
class FeeCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "school", "is_active")
    list_filter = ("school", "is_active")
    search_fields = ("name", "code")


class FeeStructureItemInline(admin.TabularInline):
    model = FeeStructureItem
    extra = 1


@admin.register(FeeStructure)
class FeeStructureAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "term", "class_group", "program", "is_active")
    list_filter = ("school", "academic_year", "term", "is_active")
    search_fields = ("name",)
    inlines = [FeeStructureItemInline]


@admin.register(FeeConcession)
class FeeConcessionAdmin(admin.ModelAdmin):
    list_display = (
        "student", "concession_type", "academic_year", "term",
        "percentage", "fixed_amount", "approved_by", "is_active",
    )
    list_filter = ("concession_type", "academic_year", "is_active")
    search_fields = ("student__admission_number",)


class InvoiceLineItemInline(admin.TabularInline):
    model = InvoiceLineItem
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    """Generation is via smsApp.services.generate_invoice_for_student() so
    line items and total_amount stay consistent — editing total_amount
    directly here would desync it from the line items."""

    list_display = (
        "invoice_number", "student", "academic_year", "term",
        "total_amount", "status", "due_date",
    )
    list_filter = ("status", "academic_year", "term")
    search_fields = ("invoice_number", "student__admission_number")
    inlines = [InvoiceLineItemInline]
    readonly_fields = ("invoice_number", "total_amount")

    def save_model(self, request, obj, form, change):
        # The invoice number is generated by Invoice.save(). total_amount is
        # derived from inline line items, which Django saves afterwards.
        if obj.total_amount is None:
            obj.total_amount = 0
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        invoice = form.instance
        total = invoice.line_items.aggregate(
            total=Sum(
                Case(
                    When(line_type=InvoiceLineItem.LineType.FEE, then=F("amount")),
                    default=-F("amount"),
                    output_field=DecimalField(max_digits=10, decimal_places=2),
                )
            )
        )["total"] or 0
        invoice.total_amount = max(total, 0)
        invoice.save(update_fields=["total_amount"])


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Recording is via smsApp.services.record_payment() so the invoice
    status and Receipt stay in sync — this view is for oversight."""

    list_display = (
        "payment_number", "invoice", "amount", "payment_method",
        "status", "payment_date",
    )
    list_filter = ("payment_method", "status")
    search_fields = ("payment_number", "invoice__invoice_number", "gateway_reference")
    readonly_fields = ("payment_number",)


@admin.register(Receipt)
class ReceiptAdmin(admin.ModelAdmin):
    """Created only by smsApp.services.record_payment() — never manually."""

    list_display = ("receipt_number", "payment", "issued_by", "issued_at")
    search_fields = ("receipt_number",)
    readonly_fields = ("receipt_number", "payment", "issued_by", "issued_at")

    def has_add_permission(self, request):
        return False


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    """Approve/reject only via smsApp.services.decide_refund() so the
    linked invoice's status is recomputed consistently."""

    list_display = ("refund_number", "payment", "amount", "status", "requested_at")
    list_filter = ("status",)
    search_fields = ("refund_number", "payment__payment_number")
    readonly_fields = ("refund_number",)


@admin.register(FinancialAdjustment)
class FinancialAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "adjustment_type", "amount", "created_by", "created_at")
    list_filter = ("adjustment_type",)
    search_fields = ("invoice__invoice_number",)


@admin.register(LibrarySettings)
class LibrarySettingsAdmin(admin.ModelAdmin):
    list_display = ("school", "loan_period_days", "max_books_per_student", "fine_per_day")


@admin.register(BookCategory)
class BookCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "school")
    list_filter = ("school",)
    search_fields = ("name",)


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Publisher)
class PublisherAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


class BookCopyInline(admin.TabularInline):
    model = BookCopy
    extra = 1


@admin.register(Book)
class BookAdmin(admin.ModelAdmin):
    list_display = ("title", "isbn", "category", "publisher", "publication_year", "is_active")
    list_filter = ("school", "category", "is_active")
    search_fields = ("title", "isbn")
    filter_horizontal = ("authors",)
    inlines = [BookCopyInline]


@admin.register(BookCopy)
class BookCopyAdmin(admin.ModelAdmin):
    list_display = ("accession_number", "book", "condition", "status", "shelf_location")
    list_filter = ("status", "condition")
    search_fields = ("accession_number", "book__title")


@admin.register(Borrowing)
class BorrowingAdmin(admin.ModelAdmin):
    """Issuing/returning goes through smsApp.services.borrow_book() /
    return_book() so BookCopy.status and fines stay consistent — this
    view is for oversight, not the primary circulation-desk workflow."""

    list_display = (
        "book_copy", "student", "staff", "status", "borrowed_date",
        "due_date", "returned_date", "fine_amount", "fine_paid",
    )
    list_filter = ("status", "fine_paid")
    search_fields = ("book_copy__accession_number", "student__admission_number", "staff__staff_id")


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "room_type", "capacity", "is_active")
    list_filter = ("school", "room_type", "is_active")
    search_fields = ("name",)


@admin.register(Period)
class PeriodAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "start_time", "end_time", "order", "is_break")
    list_filter = ("school", "is_break")
    search_fields = ("name",)


@admin.register(TimetableSlot)
class TimetableSlotAdmin(admin.ModelAdmin):
    """Scheduling should go through smsApp.services.create_timetable_slot()
    / reschedule_timetable_slot() so the three conflict checks (teacher,
    room, class double-booking) run before a slot lands — creating rows
    directly here still hits the DB constraints as a safety net, but
    without the friendly per-conflict-type error messages."""

    list_display = (
        "class_group", "teaching_assignment", "day_of_week", "period", "room", "term",
    )
    list_filter = ("term", "day_of_week", "room")
    search_fields = ("class_group__name", "teacher__staff_id")
    readonly_fields = ("term", "teacher", "class_group")


@admin.register(Announcement)
class AnnouncementAdmin(admin.ModelAdmin):
    """Publishing is via smsApp.services.create_announcement() so the
    role-aware Notification fan-out actually happens — creating a row
    directly here does NOT notify anyone."""

    list_display = ("title", "school", "audience", "is_published", "created_by", "created_at")
    list_filter = ("school", "audience", "is_published")
    search_fields = ("title",)


class NotificationDeliveryInline(admin.TabularInline):
    model = NotificationDelivery
    extra = 0
    readonly_fields = ("channel", "status", "provider_reference", "error_message", "sent_at")

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("recipient", "notification_type", "title", "is_read", "created_at")
    list_filter = ("notification_type", "is_read")
    search_fields = ("recipient__username", "title")
    inlines = [NotificationDeliveryInline]


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "in_app_enabled", "email_enabled", "sms_enabled", "push_enabled")
    search_fields = ("user__username",)


@admin.register(StaffAttendanceRecord)
class StaffAttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("staff", "date", "status", "check_in_time", "check_out_time")
    list_filter = ("status", "date")
    search_fields = ("staff__staff_id",)


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    """Approve/reject via smsApp.services.decide_leave_request() so the
    required notification (spec §6) actually gets sent — editing status
    directly here skips that."""

    list_display = (
        "staff", "leave_type", "start_date", "end_date", "status", "requested_at",
    )
    list_filter = ("leave_type", "status")
    search_fields = ("staff__staff_id",)
