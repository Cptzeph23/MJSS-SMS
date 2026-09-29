# Absolute path: SMS/smsApp/services.py
"""
Business logic / service layer.

Per spec §3 'Separation of concerns', views must not contain business logic
directly — they call into functions here. This keeps logic reusable between
Django views today and DRF API views later (§3 'API-first architecture').
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from django.http import HttpRequest

from .models import Assessment, AuditLog, ClassSubject, LoginHistory, Staff, Student, Term, User


def _client_ip(request: HttpRequest | None) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_audit(
    *,
    actor: User | None,
    action: str,
    request: HttpRequest | None = None,
    target_model: str = "",
    target_object_id: str | int = "",
    description: str = "",
    previous_value: dict[str, Any] | None = None,
    new_value: dict[str, Any] | None = None,
) -> AuditLog:
    """Single write path for the audit trail (spec §5, §27, §33, §37, §38).
    Every sensitive action (role changes, approvals, financial adjustments,
    account lock/unlock, etc.) must go through this function rather than
    writing to AuditLog directly, so the shape stays consistent."""
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target_model=target_model,
        target_object_id=str(target_object_id) if target_object_id != "" else "",
        description=description,
        previous_value=previous_value,
        new_value=new_value,
        ip_address=_client_ip(request),
    )


def record_login(
    *, user: User, request: HttpRequest | None, was_successful: bool
) -> LoginHistory:
    """Spec §5 'View login history'. Called from the login view (Phase 4)."""
    user_agent = request.META.get("HTTP_USER_AGENT", "")[:255] if request else ""
    entry = LoginHistory.objects.create(
        user=user,
        ip_address=_client_ip(request),
        user_agent=user_agent,
        was_successful=was_successful,
    )
    log_audit(
        actor=user,
        action=AuditLog.Action.LOGIN if was_successful else AuditLog.Action.LOGIN_FAILED,
        request=request,
        target_model="User",
        target_object_id=user.pk,
        description="User logged in" if was_successful else "Failed login attempt",
    )
    return entry


def get_dashboard_url_for_role(user: User) -> str:
    """Central place mapping a User -> dashboard URL name.
    Used by the post-login router (Phase 4) and kept here, not hard-coded
    in views, so Phase 6+ dashboards only need one line added here.

    Accepts the full user (not just `role`) because `is_superuser` must
    win regardless of `role` — see User.save() docstring for why `role`
    alone can't be trusted for accounts created via createsuperuser."""
    from django.urls import reverse

    if user.is_superuser:
        return reverse("dashboard:super_admin")

    mapping = {
        User.Role.SUPER_ADMIN: "dashboard:super_admin",
        User.Role.STUDENT: "dashboard:student_dashboard",
        User.Role.PARENT: "dashboard:parent_dashboard",
        User.Role.TEACHER: "dashboard:teacher_dashboard",
        User.Role.CLASS_TEACHER: "dashboard:teacher_dashboard",
        User.Role.FINANCE_ADMIN: "dashboard:finance_dashboard",
        User.Role.ACCOUNTANT: "dashboard:finance_dashboard",
        User.Role.STAFF_ADMIN: "dashboard:staff_admin_dashboard",
        User.Role.ACADEMIC_ADMIN: "dashboard:academic_admin_dashboard",
        # Other roles route here as their dashboards are built
        # (Department Head, Exam Officer, Librarian, etc.)
    }
    url_name = mapping.get(user.role, "dashboard:coming_soon")
    return reverse(url_name)


def correct_attendance_record(
    *,
    record: "AttendanceRecord",
    new_status: str,
    corrected_by: User,
    request: HttpRequest | None = None,
    new_notes: str | None = None,
) -> "AttendanceRecord":
    """Spec §11: 'Academic Admin can... correct attendance with appropriate
    permissions'. This is the single write path for corrections — callers
    (views, Phase 7+) must not set `record.status = ...; record.save()`
    directly, or the correction won't be captured in AuditLog (spec §5,
    §27, §33 all require before/after values for sensitive edits).

    Caller is responsible for the actual permission check (e.g.
    RoleRequiredMixin on the view) — this function only records what
    changed, it does not decide who is allowed to call it.
    """
    previous_value = {"status": record.status, "notes": record.notes}

    record.status = new_status
    if new_notes is not None:
        record.notes = new_notes
    record.recorded_by = corrected_by
    record.save(update_fields=["status", "notes", "recorded_by", "updated_at"])

    log_audit(
        actor=corrected_by,
        action=AuditLog.Action.UPDATE,
        request=request,
        target_model="AttendanceRecord",
        target_object_id=record.pk,
        description=f"Corrected attendance for {record.student} on {record.session.date}",
        previous_value=previous_value,
        new_value={"status": record.status, "notes": record.notes},
    )
    return record


# =============================================================================
# Phase 7 — Grading engine (spec §13). All lookups are data-driven off
# GradingScheme/GradeBand/AssessmentComponent — nothing here hard-codes a
# grade boundary or a weighting percentage.
# =============================================================================

def get_grade_for_mark(scheme, mark) -> "GradeBand | None":
    """Spec §13: resolve a numeric mark to a GradeBand under the given
    scheme. Returns None if no band covers the mark (a configuration gap
    the caller/UI should surface, not silently default from)."""
    return scheme.bands.filter(min_mark__lte=mark, max_mark__gte=mark).first()


def validate_grade_bands_no_overlap(scheme) -> list[str]:
    """Returns a list of human-readable overlap errors for the scheme's
    bands, empty if none. Not a DB constraint (cross-row check) — called
    from the admin/service layer before treating a scheme as usable."""
    bands = list(scheme.bands.order_by("min_mark"))
    errors: list[str] = []
    for i in range(len(bands) - 1):
        current, nxt = bands[i], bands[i + 1]
        if current.max_mark >= nxt.min_mark:
            errors.append(
                f"'{current.grade}' ({current.min_mark}-{current.max_mark}) overlaps "
                f"'{nxt.grade}' ({nxt.min_mark}-{nxt.max_mark})"
            )
    return errors


def compute_weighted_average(
    student, class_subject, term, *, published_only: bool = False
) -> dict[str, Any]:
    """Spec §12/§13: weighted total across all Assessments for a student
    in a ClassSubject/Term, using each Assessment's linked
    AssessmentComponent for weight and max_marks — no percentages are
    hard-coded here, they're read entirely from the configured structure.

    `published_only=True` restricts to Assessments whose workflow has
    reached PUBLISHED (spec §14) — used by transcript generation (Phase
    10), which must never include draft/unapproved marks in an official
    academic record. Report books (Phase 9) intentionally leave this
    False, since a term-in-progress report legitimately shows draft marks.

    Returns a dict rather than a bare number because callers (report book,
    Phase 15) need the raw weighted score, the count of graded components,
    and whether every configured component has a mark yet.
    """
    assessments = (
        class_subject.assessments
        .filter(term=term)
        .select_related("component")
    )
    if published_only:
        assessments = assessments.filter(workflow_status=Assessment.WorkflowStatus.PUBLISHED)

    assessments = list(assessments)
    # Fetch all marks in one query; per-assessment lookups are expensive over Supabase.
    from .models import AssessmentMark
    marks_by_assessment = {
        mark.assessment_id: mark
        for mark in AssessmentMark.objects.filter(
            assessment_id__in=[assessment.pk for assessment in assessments],
            student_id=student.pk,
        )
    }

    weighted_total = Decimal("0")
    weight_covered = Decimal("0")
    components_graded = 0
    components_total = len(assessments)

    for assessment in assessments:
        mark_row = marks_by_assessment.get(assessment.pk)
        if mark_row is None:
            continue
        component = assessment.component
        if component.max_marks <= 0:
            continue
        proportion = mark_row.marks_obtained / component.max_marks
        weighted_total += proportion * component.weight_percentage
        weight_covered += component.weight_percentage
        components_graded += 1

    return {
        "weighted_total": weighted_total.quantize(Decimal("0.01")),
        "weight_covered": weight_covered.quantize(Decimal("0.01")),
        "components_graded": components_graded,
        "components_total": components_total,
        "is_complete": components_graded == components_total and components_total > 0,
    }


def compute_student_subject_completion(*, student, class_subject, term) -> dict[str, int | bool]:
    """Return completion across every task type for one subject/term.

    Formal Assessment rows, assignment submissions, and interactive Quiz
    attempts are stored separately. New CAT/exam tasks create both a formal
    Assessment row and an interactive Quiz row, so matching titles are
    counted once through the Quiz attempt rather than blocking completion
    until a duplicate manual mark is entered.
    """
    from .models import Assessment, Assignment, AssignmentSubmission, Quiz, QuizAttempt

    quizzes = Quiz.objects.filter(
        class_subject=class_subject, term=term, is_published=True
    )
    quiz_titles = quizzes.values_list("title", flat=True)
    assessments = Assessment.objects.filter(
        class_subject=class_subject, term=term
    ).exclude(title__in=quiz_titles)
    assignments = Assignment.objects.filter(
        class_subject=class_subject, term=term, is_published=True
    )

    assessment_total = assessments.count()
    assessment_done = assessments.filter(
        marks__student=student, marks__marks_obtained__isnull=False
    ).distinct().count()
    assignment_total = assignments.count()
    assignment_done = AssignmentSubmission.objects.filter(
        assignment__in=assignments, student=student,
        marks_obtained__isnull=False,
        status=AssignmentSubmission.Status.GRADED,
    ).values("assignment_id").distinct().count()
    quizzes = list(quizzes)
    quiz_total = len(quizzes)
    latest_attempts = {}
    for attempt in QuizAttempt.objects.filter(
        quiz__in=quizzes, student=student, is_fully_graded=True
    ).order_by("quiz_id", "-attempt_number"):
        latest_attempts.setdefault(attempt.quiz_id, attempt)
    quiz_done = sum(
        1 for quiz in quizzes
        if (attempt := latest_attempts.get(quiz.pk)) is not None
        and attempt.total_score is not None
    )

    total = assessment_total + assignment_total + quiz_total
    completed = assessment_done + assignment_done + quiz_done
    return {
        "total_tasks": total,
        "completed_tasks": completed,
        "is_complete": total > 0 and completed == total,
    }


# =============================================================================
# Phase 8 — Result Processing Workflow (spec §14)
# DRAFT -> SUBMITTED -> REVIEWED -> VERIFIED -> APPROVED -> PUBLISHED.
# Every transition is a separate, narrow function so each pipeline stage
# can be permission-checked independently by the calling view (e.g. only
# a Class Teacher may call review_assessment, only Academic Admin may call
# verify_assessment) — this module does not decide who is allowed to call
# it, only that the *sequence* is respected and every step is audited.
# =============================================================================

_WORKFLOW_ORDER = [
    "DRAFT", "SUBMITTED", "REVIEWED", "VERIFIED", "APPROVED", "PUBLISHED",
]


def _require_status(assessment, expected: str) -> None:
    if assessment.workflow_status != expected:
        raise ValueError(
            f"Cannot perform this transition from status "
            f"'{assessment.workflow_status}' — expected '{expected}'."
        )


def transition_assessment_workflow(
    *,
    assessment: "Assessment",
    to_status: str,
    actor: User,
    request: HttpRequest | None = None,
) -> "Assessment":
    """Single write path for every workflow stage change. `to_status` must
    be the next status in _WORKFLOW_ORDER (no skipping stages, no going
    backwards except via explicit rejection — see reject_assessment).

    Enforces spec §9: 'Teachers must not be able to approve their own
    final results' — the actor who submitted an assessment cannot also
    be the one who approves it.
    """
    from django.utils import timezone

    current_index = _WORKFLOW_ORDER.index(assessment.workflow_status)
    try:
        target_index = _WORKFLOW_ORDER.index(to_status)
    except ValueError:
        raise ValueError(f"'{to_status}' is not a valid forward workflow status.")

    if target_index != current_index + 1:
        raise ValueError(
            f"Cannot jump from '{assessment.workflow_status}' to '{to_status}' — "
            f"stages must be completed in order."
        )

    if to_status == Assessment.WorkflowStatus.APPROVED and assessment.submitted_by_id == actor.pk:
        raise PermissionError(
            "A teacher cannot approve their own submitted results — "
            "independent approval is required (spec §9)."
        )

    now = timezone.now()
    field_map = {
        "SUBMITTED": ("submitted_by", "submitted_at"),
        "REVIEWED": ("reviewed_by", "reviewed_at"),
        "VERIFIED": ("verified_by", "verified_at"),
        "APPROVED": ("approved_by", "approved_at"),
        "PUBLISHED": ("published_by", "published_at"),
    }
    actor_field, timestamp_field = field_map[to_status]
    setattr(assessment, actor_field, actor)
    setattr(assessment, timestamp_field, now)
    assessment.workflow_status = to_status
    if to_status == Assessment.WorkflowStatus.PUBLISHED:
        assessment.is_published = True

    assessment.save()

    log_audit(
        actor=actor,
        action=AuditLog.Action.PUBLISH if to_status == "PUBLISHED" else AuditLog.Action.APPROVE,
        request=request,
        target_model="Assessment",
        target_object_id=assessment.pk,
        description=f"Assessment moved to '{to_status}'",
        previous_value={"workflow_status": _WORKFLOW_ORDER[current_index]},
        new_value={"workflow_status": to_status},
    )
    return assessment


def reject_assessment(
    *, assessment: "Assessment", actor: User, reason: str, request: HttpRequest | None = None
) -> "Assessment":
    """Sends an assessment back to DRAFT for correction, from any
    in-progress stage (not from PUBLISHED — use amendment requests
    instead, since published results must not be silently reopened)."""
    if assessment.workflow_status in (
        Assessment.WorkflowStatus.DRAFT, Assessment.WorkflowStatus.PUBLISHED,
    ):
        raise ValueError(
            f"Cannot reject an assessment in '{assessment.workflow_status}' status."
        )

    previous_status = assessment.workflow_status
    assessment.workflow_status = Assessment.WorkflowStatus.DRAFT
    assessment.save(update_fields=["workflow_status", "updated_at"])

    log_audit(
        actor=actor,
        action=AuditLog.Action.UPDATE,
        request=request,
        target_model="Assessment",
        target_object_id=assessment.pk,
        description=f"Assessment rejected and returned to Draft: {reason}",
        previous_value={"workflow_status": previous_status},
        new_value={"workflow_status": "DRAFT"},
    )
    return assessment


def request_result_amendment(
    *,
    assessment_mark: "AssessmentMark",
    reason: str,
    proposed_mark,
    requested_by: User,
    request: HttpRequest | None = None,
) -> "ResultAmendmentRequest":
    """Spec §14: the only way to change a mark once its Assessment has
    been PUBLISHED. Captures original_mark as a snapshot so the audit
    trail is accurate even if the mark changes again before this is
    reviewed."""
    from .models import ResultAmendmentRequest

    amendment = ResultAmendmentRequest.objects.create(
        assessment_mark=assessment_mark,
        reason=reason,
        original_mark=assessment_mark.marks_obtained,
        proposed_mark=proposed_mark,
        requested_by=requested_by,
    )
    log_audit(
        actor=requested_by,
        action=AuditLog.Action.OTHER,
        request=request,
        target_model="ResultAmendmentRequest",
        target_object_id=amendment.pk,
        description=reason,
        previous_value={"mark": str(assessment_mark.marks_obtained)},
        new_value={"proposed_mark": str(proposed_mark)},
    )
    return amendment


def decide_result_amendment(
    *,
    amendment: "ResultAmendmentRequest",
    approve: bool,
    reviewed_by: User,
    comment: str = "",
    request: HttpRequest | None = None,
) -> "ResultAmendmentRequest":
    """Applies or rejects a pending amendment. Approving is the *only*
    code path permitted to mutate marks.marks_obtained on a mark whose
    Assessment is already PUBLISHED (spec §14 'prevent unrestricted
    modification')."""
    from django.utils import timezone
    from .models import ResultAmendmentRequest

    if amendment.status != ResultAmendmentRequest.Status.PENDING:
        raise ValueError("This amendment request has already been decided.")

    amendment.reviewed_by = reviewed_by
    amendment.reviewed_at = timezone.now()
    amendment.review_comment = comment

    if approve:
        amendment.status = ResultAmendmentRequest.Status.APPROVED
        mark = amendment.assessment_mark
        previous_marks = mark.marks_obtained
        mark.marks_obtained = amendment.proposed_mark
        mark.save(update_fields=["marks_obtained", "updated_at"], _bypass_publish_lock=True)

        log_audit(
            actor=reviewed_by,
            action=AuditLog.Action.UPDATE,
            request=request,
            target_model="AssessmentMark",
            target_object_id=mark.pk,
            description=f"Amendment approved: {amendment.reason}",
            previous_value={"marks_obtained": str(previous_marks)},
            new_value={"marks_obtained": str(mark.marks_obtained)},
        )
    else:
        amendment.status = ResultAmendmentRequest.Status.REJECTED
        log_audit(
            actor=reviewed_by,
            action=AuditLog.Action.OTHER,
            request=request,
            target_model="ResultAmendmentRequest",
            target_object_id=amendment.pk,
            description=f"Amendment rejected: {comment}",
        )

    amendment.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_comment"])
    return amendment


# =============================================================================
# Phase 9 — Report Book System (spec §15). All layout lives in
# templates/reports/*.html; nothing here hard-codes HTML/positioning —
# this module only assembles the data dict the template renders.
# =============================================================================

def compute_class_term_rankings(*, class_group, term) -> dict[int, dict[str, Any]]:
    """Spec §13: 'Make ranking configurable because some schools may
    choose not to rank students' — callers must check
    class_group.school.enable_position_ranking before using this for
    display; it's computed unconditionally here so the check stays a
    presentation decision, not a data-availability one.

    Returns {student_id: {"average": Decimal, "position": int}} across
    every student currently in the class, ranked by their average
    percentage across all of their ClassSubjects for the term.
    """
    students = Student.objects.filter(current_class=class_group, is_active=True)
    averages: list[tuple[int, Decimal]] = []

    for student in students:
        class_subjects = ClassSubject.objects.filter(
            enrollments__student=student, enrollments__academic_year=term.academic_year
        ).distinct()
        if not class_subjects:
            continue
        totals = [
            compute_weighted_average(student, cs, term)["weighted_total"]
            for cs in class_subjects
        ]
        if totals:
            averages.append((student.pk, sum(totals) / len(totals)))

    averages.sort(key=lambda pair: pair[1], reverse=True)

    results: dict[int, dict[str, Any]] = {}
    for position, (student_id, average) in enumerate(averages, start=1):
        results[student_id] = {"average": average, "position": position}
    return results


def assemble_report_data(*, student: "Student", term: "Term") -> dict[str, Any]:
    """Spec §15: gathers every field the report layout needs — school
    identity, student identity, per-subject marks/grades, attendance,
    position (if enabled) — into one plain dict. The template decides how
    to lay it out; this function never renders HTML or decides layout."""
    from .models import AttendanceRecord, GradingScheme

    school = student.school
    class_subjects = ClassSubject.objects.filter(
        enrollments__student=student, enrollments__academic_year=term.academic_year
    ).distinct().select_related("subject")

    grading_scheme = GradingScheme.objects.filter(school=school, is_default=True).first()

    subject_rows = []
    percentage_totals = []
    for class_subject in class_subjects:
        summary = compute_weighted_average(student, class_subject, term)
        band = None
        if grading_scheme and summary["weight_covered"] > 0:
            band = get_grade_for_mark(grading_scheme, summary["weighted_total"])
        subject_rows.append(
            {
                "subject": class_subject.subject.name,
                "score": summary["weighted_total"],
                "grade": band.grade if band else "-",
                "grade_point": band.grade_point if band else None,
                "is_complete": summary["is_complete"],
            }
        )
        if summary["weight_covered"] > 0:
            percentage_totals.append(summary["weighted_total"])

    average = (
        (sum(percentage_totals) / len(percentage_totals)).quantize(Decimal("0.01"))
        if percentage_totals else None
    )

    ranking = None
    if school.enable_position_ranking and student.current_class_id:
        rankings = compute_class_term_rankings(class_group=student.current_class, term=term)
        ranking = rankings.get(student.pk)

    attendance_qs = AttendanceRecord.objects.filter(
        student=student, session__term=term
    )
    attendance_summary = {
        "present": attendance_qs.filter(status="PRESENT").count(),
        "absent": attendance_qs.filter(status="ABSENT").count(),
        "late": attendance_qs.filter(status="LATE").count(),
        "excused": attendance_qs.filter(status="EXCUSED").count(),
    }

    return {
        "school": school,
        "student": student,
        "class_group": student.current_class,
        "stream": student.current_stream,
        "academic_year": term.academic_year,
        "term": term,
        "subject_rows": subject_rows,
        "average": average,
        "position": ranking["position"] if ranking else None,
        "class_size": len(
            [k for k in (compute_class_term_rankings(
                class_group=student.current_class, term=term
            ) if student.current_class_id else {})]
        ) if ranking else None,
        "attendance_summary": attendance_summary,
    }


def render_report_html(*, report_card: "ReportCard") -> str:
    """Renders report_card's configured template with freshly assembled
    data. Template choice and which sections to show come entirely from
    ReportTemplate (spec §15 'Allow report templates to be configurable.
    Do not hard-code the report layout into business logic') — this
    function contains no layout decisions itself."""
    from django.template.loader import render_to_string

    template_paths = {
        "DEFAULT": "reports/default.html",
    }
    template_path = template_paths[report_card.template.template_key]

    context = assemble_report_data(student=report_card.student, term=report_card.term)
    context["school_logo"] = _school_logo_data_uri(context["school"])
    context.update(
        {
            "template_config": report_card.template,
            "class_teacher_comment": report_card.class_teacher_comment,
            "principal_comment": report_card.principal_comment,
        }
    )
    return render_to_string(template_path, context)


def _school_logo_data_uri(school) -> str:
    """Return the configured school logo as an embeddable image URI.

    Report PDFs are rendered by WeasyPrint without a browser request context;
    a private/relative storage URL can therefore become a broken image. The
    logo is read through Django's configured storage and embedded directly so
    both HTML previews and PDFs use the same reliable asset.
    """
    import base64
    import mimetypes

    if not school or not school.logo:
        return ""
    try:
        school.logo.open("rb")
        contents = school.logo.read()
        school.logo.close()
    except (OSError, ValueError):
        return ""
    content_type = mimetypes.guess_type(school.logo.name)[0] or "image/jpeg"
    return f"data:{content_type};base64,{base64.b64encode(contents).decode('ascii')}"


def generate_report_pdf(
    *, report_card: "ReportCard", generated_by: User, request: HttpRequest | None = None
) -> "ReportCard":
    """Spec §15 'PDF report' / 'Downloadable report'. Renders via
    render_report_html() then converts with WeasyPrint (HTML/CSS -> PDF),
    so the PDF and the on-screen HTML report always come from the exact
    same template and data-assembly code — no separate PDF-only layout
    to fall out of sync."""
    from django.core.files.base import ContentFile
    from django.utils import timezone
    from weasyprint import HTML

    html_content = render_report_html(report_card=report_card)
    pdf_bytes = HTML(string=html_content).write_pdf()

    filename = f"report_{report_card.student.admission_number}_{report_card.term_id}.pdf"
    report_card.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
    report_card.generated_by = generated_by
    report_card.generated_at = timezone.now()
    report_card.save()

    log_audit(
        actor=generated_by,
        action=AuditLog.Action.OTHER,
        request=request,
        target_model="ReportCard",
        target_object_id=report_card.pk,
        description=f"Generated report PDF for {report_card.student}",
    )
    return report_card


def generate_batch_reports(
    *,
    class_group,
    term: "Term",
    template: "ReportTemplate",
    generated_by: User,
    request: HttpRequest | None = None,
) -> list["ReportCard"]:
    """Spec §15 'Batch reports'. Creates/updates one ReportCard per active
    student in the class and generates each PDF. Returns the list so the
    calling view can present a summary/zip download."""
    from .models import ReportCard as ReportCardModel

    cards = []
    for student in Student.objects.filter(current_class=class_group, is_active=True):
        card, _ = ReportCardModel.objects.get_or_create(
            student=student, term=term, template=template
        )
        generate_report_pdf(report_card=card, generated_by=generated_by, request=request)
        cards.append(card)
    return cards


# =============================================================================
# Phase 10 — Transcript System (spec §16). See Transcript/TranscriptEntry
# docstrings in models.py for why entries are snapshotted rather than
# recomputed live, and for the "secure PDF" interpretation.
# =============================================================================

def generate_transcript(
    *, student: "Student", generated_by: User, request: HttpRequest | None = None
) -> "Transcript":
    """Builds a full cumulative transcript from every PUBLISHED assessment
    across every term/class_subject the student has been enrolled in,
    snapshots it into TranscriptEntry rows, computes GPA/CGPA, renders the
    PDF, and stamps a verification_code + content_hash."""
    import hashlib

    from django.core.files.base import ContentFile
    from django.template.loader import render_to_string
    from django.utils import timezone
    from weasyprint import HTML

    from .models import ClassSubject as ClassSubjectModel, GradingScheme, Transcript as TranscriptModel

    school = student.school
    grading_scheme = GradingScheme.objects.filter(school=school, is_default=True).first()

    class_subjects = (
        ClassSubjectModel.objects.filter(enrollments__student=student)
        .distinct()
        .select_related("subject", "class_group")
    )

    entry_rows = []
    latest_term = None
    for class_subject in class_subjects:
        terms = Term.objects.filter(
            assessments__class_subject=class_subject,
            assessments__marks__student=student,
            assessments__workflow_status=Assessment.WorkflowStatus.PUBLISHED,
        ).distinct()

        for term in terms:
            summary = compute_weighted_average(
                student, class_subject, term, published_only=True
            )
            if summary["components_graded"] == 0:
                continue

            band = None
            if grading_scheme:
                band = get_grade_for_mark(grading_scheme, summary["weighted_total"])

            entry_rows.append(
                {
                    "subject": class_subject.subject,
                    "subject_name": class_subject.subject.name,
                    "academic_year_label": term.academic_year.name,
                    "term_label": term.name,
                    "score": summary["weighted_total"],
                    "grade": band.grade if band else "",
                    "grade_point": band.grade_point if band else None,
                    "credit_hours": class_subject.subject.credit_hours,
                    "term_obj": term,
                }
            )
            if latest_term is None or term.start_date > latest_term.start_date:
                latest_term = term

    def _grade_points(rows):
        return [r["grade_point"] for r in rows if r["grade_point"] is not None]

    cgpa = None
    all_points = _grade_points(entry_rows)
    if all_points:
        weighted_sum = Decimal("0")
        weight_sum = Decimal("0")
        for row in entry_rows:
            if row["grade_point"] is None:
                continue
            credit = row["credit_hours"] or Decimal("1")
            weighted_sum += row["grade_point"] * credit
            weight_sum += credit
        cgpa = (weighted_sum / weight_sum).quantize(Decimal("0.01")) if weight_sum else None

    gpa = None
    if latest_term is not None:
        latest_points = _grade_points(
            [r for r in entry_rows if r["term_obj"] == latest_term]
        )
        if latest_points:
            gpa = (sum(latest_points) / len(latest_points)).quantize(Decimal("0.01"))

    graduation_status = TranscriptModel.GraduationStatus.IN_PROGRESS
    if student.status == Student.Status.GRADUATED:
        graduation_status = TranscriptModel.GraduationStatus.GRADUATED
    elif student.status in (
        Student.Status.WITHDRAWN, Student.Status.EXPELLED, Student.Status.TRANSFERRED,
    ):
        graduation_status = TranscriptModel.GraduationStatus.NOT_GRADUATED

    transcript = TranscriptModel.objects.create(
        student=student,
        generated_by=generated_by,
        academic_status=student.status,
        graduation_status=graduation_status,
        gpa=gpa,
        cgpa=cgpa,
    )

    for row in entry_rows:
        transcript.entries.create(
            subject=row["subject"],
            subject_name=row["subject_name"],
            academic_year_label=row["academic_year_label"],
            term_label=row["term_label"],
            score=row["score"],
            grade=row["grade"],
            grade_point=row["grade_point"],
            credit_hours=row["credit_hours"],
        )

    # Content hash over a canonical representation of what was issued, so
    # a later dispute can prove whether a PDF matches what was generated.
    canonical = "|".join(
        f"{r['subject_name']}:{r['term_label']}:{r['score']}:{r['grade']}"
        for r in sorted(entry_rows, key=lambda r: (r["academic_year_label"], r["term_label"], r["subject_name"]))
    )
    canonical += f"|gpa={gpa}|cgpa={cgpa}|status={student.status}"
    transcript.content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    transcript.save(update_fields=["content_hash"])

    html_content = render_to_string(
        "reports/transcript.html",
        {
            "school": school,
            "school_logo": _school_logo_data_uri(school),
            "student": student,
            "transcript": transcript,
            "entries": transcript.entries.all(),
        },
    )
    pdf_bytes = HTML(string=html_content).write_pdf()
    transcript.pdf_file.save(
        f"transcript_{student.admission_number}_{transcript.pk}.pdf",
        ContentFile(pdf_bytes), save=True,
    )

    log_audit(
        actor=generated_by,
        action=AuditLog.Action.OTHER,
        request=request,
        target_model="Transcript",
        target_object_id=transcript.pk,
        description=f"Generated transcript for {student}",
    )
    return transcript


def verify_transcript(verification_code) -> dict[str, Any]:
    """Spec §16 'Generate secure PDF documents' — the verification half of
    that: given the UUID printed on an issued transcript, confirm it's
    genuine without exposing the full academic record to whoever holds
    the code."""
    from .models import Transcript as TranscriptModel

    transcript = TranscriptModel.objects.filter(verification_code=verification_code).first()
    if transcript is None:
        return {"valid": False}

    return {
        "valid": True,
        "student_name": transcript.student.user.get_full_name()
        or transcript.student.user.username,
        "admission_number": transcript.student.admission_number,
        "generated_at": transcript.generated_at,
        "cgpa": transcript.cgpa,
        "graduation_status": transcript.get_graduation_status_display(),
    }


# =============================================================================
# Phase 11 — LMS Module (spec §10)
# =============================================================================

def submit_assignment(
    *,
    assignment: "Assignment",
    student: Student,
    submitted_file=None,
    submitted_text: str = "",
    request: HttpRequest | None = None,
):
    """Spec §10 'Upload submissions' / 'Resubmit where permitted'. Single
    write path for both first submission and resubmission — enforces:
    - a first submission is always allowed (while published);
    - a second+ submission requires assignment.allow_resubmission;
    - `is_late` is computed against the deadline at submission time and
      never recomputed later, so it stays an honest historical record
      even if the deadline is edited afterward.
    """
    from django.utils import timezone

    from .models import AssignmentSubmission

    existing = AssignmentSubmission.objects.filter(
        assignment=assignment, student=student
    ).first()

    if existing is not None and existing.attempt_number >= assignment.max_attempts:
        raise ValueError(
            "You have used all allowed attempts for this assignment."
        )
    if existing is not None and not assignment.allow_resubmission and assignment.max_attempts <= 1:
        raise ValueError("This assignment does not allow resubmission.")

    now = timezone.now()
    is_late = now > assignment.deadline

    if existing is None:
        submission = AssignmentSubmission.objects.create(
            assignment=assignment, student=student,
            submitted_file=submitted_file, submitted_text=submitted_text,
            is_late=is_late, status=AssignmentSubmission.Status.SUBMITTED,
        )
    else:
        previous_value = {
            "submitted_text": existing.submitted_text,
            "attempt_number": existing.attempt_number,
        }
        existing.submitted_file = submitted_file
        existing.submitted_text = submitted_text
        existing.attempt_number += 1
        existing.is_late = is_late
        existing.status = AssignmentSubmission.Status.RESUBMITTED
        # A resubmission supersedes any prior grade — re-grading is required.
        existing.marks_obtained = None
        existing.feedback = ""
        existing.graded_by = None
        existing.graded_at = None
        existing.save()
        submission = existing

        log_audit(
            actor=student.user, action=AuditLog.Action.UPDATE, request=request,
            target_model="AssignmentSubmission", target_object_id=submission.pk,
            description=f"Resubmitted {assignment.title}",
            previous_value=previous_value,
            new_value={"attempt_number": submission.attempt_number},
        )

    if assignment.created_by_id:
        send_notification(
            recipient=assignment.created_by.user,
            notification_type="SUBMISSION_RECEIVED",
            title="New assignment submission",
            body=f"{student} submitted {assignment.title}.",
            related_model="Assignment", related_object_id=assignment.pk, request=request,
        )

    return submission


def grade_assignment_submission(
    *,
    submission: "AssignmentSubmission",
    marks_obtained: Decimal,
    feedback: str,
    graded_by: Staff,
    request: HttpRequest | None = None,
):
    """Spec §10 'Mark assignments' / 'Provide feedback' (Teacher Dashboard,
    §9)."""
    from django.utils import timezone

    from .models import AssignmentSubmission

    if marks_obtained > submission.assignment.max_marks:
        raise ValueError(
            f"marks_obtained ({marks_obtained}) cannot exceed "
            f"the assignment's max_marks ({submission.assignment.max_marks})."
        )

    submission.marks_obtained = marks_obtained
    submission.feedback = feedback
    submission.graded_by = graded_by
    submission.graded_at = timezone.now()
    submission.status = AssignmentSubmission.Status.GRADED
    submission.save()

    log_audit(
        actor=graded_by.user, action=AuditLog.Action.OTHER, request=request,
        target_model="AssignmentSubmission", target_object_id=submission.pk,
        description=f"Graded submission for {submission.assignment.title}",
        new_value={"marks_obtained": str(marks_obtained)},
    )
    return submission


def submit_quiz_attempt(
    *,
    attempt: "QuizAttempt",
    answers: dict[int, dict],
) -> "QuizAttempt":
    """Spec §10 'Implement automatic marking where appropriate'.

    `answers` maps question_id -> {"option_ids": [...]} for
    MULTIPLE_CHOICE/TRUE_FALSE/MULTIPLE_ANSWER, or {"text": "..."} for
    SHORT_ANSWER.

    Auto-grades objective question types by exact-match: a MULTIPLE_CHOICE
    or TRUE_FALSE question is correct if the single selected option is the
    correct one; a MULTIPLE_ANSWER question is correct only if the
    selected set exactly equals the correct set (no partial credit in
    this MVP — see docstring note below for extending to partial credit).
    SHORT_ANSWER questions are recorded but left ungraded
    (marks_awarded=None) for manual grading.
    """
    from django.utils import timezone

    from .models import QuizAnswer

    auto_score = Decimal("0")
    has_ungraded_manual = False

    for question in attempt.quiz.questions.all():
        payload = answers.get(question.pk, {})
        answer = QuizAnswer.objects.create(attempt=attempt, question=question)
        if payload.get("file"):
            answer.submitted_file = payload["file"]

        if question.question_type == question.QuestionType.SHORT_ANSWER:
            answer.text_answer = payload.get("text", "")
            answer.marks_awarded = None
            answer.save()
            has_ungraded_manual = True
            continue

        option_ids = set(payload.get("option_ids", []))
        answer.selected_options.set(option_ids)

        correct_ids = set(
            question.options.filter(is_correct=True).values_list("pk", flat=True)
        )
        is_correct = option_ids == correct_ids
        answer.marks_awarded = question.marks if is_correct else Decimal("0")
        answer.save()
        auto_score += answer.marks_awarded

    attempt.auto_score = auto_score
    attempt.submitted_at = timezone.now()
    attempt.is_fully_graded = not has_ungraded_manual
    attempt.save()
    return attempt


def grade_quiz_short_answer(
    *, answer: "QuizAnswer", marks_awarded: Decimal
) -> "QuizAnswer":
    """Manual grading step for SHORT_ANSWER questions within an attempt.
    Once every short-answer question in the attempt has been graded,
    the attempt is marked fully graded and its manual_score is totaled."""
    if marks_awarded > answer.question.marks:
        raise ValueError(
            f"marks_awarded ({marks_awarded}) cannot exceed the "
            f"question's marks ({answer.question.marks})."
        )

    answer.marks_awarded = marks_awarded
    answer.save()

    attempt = answer.attempt
    manual_questions = attempt.quiz.questions.filter(
        question_type="SHORT_ANSWER"
    )
    manual_answers = attempt.answers.filter(question__in=manual_questions)

    if not manual_answers.filter(marks_awarded__isnull=True).exists():
        attempt.manual_score = sum(
            (a.marks_awarded or Decimal("0")) for a in manual_answers
        )
        attempt.is_fully_graded = True
        attempt.save()

    return answer


# =============================================================================
# Phase 12 — Finance (spec §19). Spec: "All financial modifications must
# be audited" and "Do not expose academic grades to Finance Admin" — every
# write path here calls log_audit(), and none of these functions touch
# AssessmentMark/SubjectResult/grades in any way.
# =============================================================================

def generate_invoice_for_student(
    *,
    student: Student,
    fee_structure,
    academic_year,
    term,
    issued_by: User,
    due_date,
    request: HttpRequest | None = None,
):
    """Spec §19 'Invoices'. Snapshots FeeStructureItem amounts into
    InvoiceLineItem rows and applies any active FeeConcession for this
    student/academic_year (Discount/Scholarship/Waiver) as negative-effect
    lines — so the invoice total is fixed at generation time and won't
    silently drift if the fee structure or concessions change later."""
    from django.utils import timezone

    from .models import FeeConcession, Invoice, InvoiceLineItem

    invoice = Invoice.objects.create(
        student=student, school=student.school, academic_year=academic_year,
        term=term, fee_structure=fee_structure, total_amount=Decimal("0"),
        issue_date=timezone.localtime(timezone.now()).date(), due_date=due_date, created_by=issued_by,
    )

    total = Decimal("0")
    for item in fee_structure.items.all():
        InvoiceLineItem.objects.create(
            invoice=invoice, category=item.category,
            line_type=InvoiceLineItem.LineType.FEE,
            description=item.category.name, amount=item.amount,
        )
        total += item.amount

    concessions = FeeConcession.objects.filter(
        student=student, academic_year=academic_year, is_active=True
    ).filter(_term_matches_q(term))

    for concession in concessions:
        if concession.percentage is not None:
            reduction = (total * concession.percentage / Decimal("100")).quantize(Decimal("0.01"))
        else:
            reduction = concession.fixed_amount
        reduction = min(reduction, total)  # never let a concession push the invoice negative
        InvoiceLineItem.objects.create(
            invoice=invoice, line_type=concession.concession_type,
            description=concession.description or concession.get_concession_type_display(),
            amount=reduction,
        )
        total -= reduction

    invoice.total_amount = max(total, Decimal("0"))
    invoice.save(update_fields=["total_amount"])

    log_audit(
        actor=issued_by, action=AuditLog.Action.CREATE, request=request,
        target_model="Invoice", target_object_id=invoice.pk,
        description=f"Generated invoice {invoice.invoice_number} for {student}",
        new_value={"total_amount": str(invoice.total_amount)},
    )
    return invoice


def _term_matches_q(term):
    """Helper: a concession applies if it's for this exact term, OR it has
    no term set (meaning it applies to the whole academic year)."""
    from django.db.models import Q
    return Q(term=term) | Q(term__isnull=True)


def _recompute_invoice_status(invoice) -> None:
    from .models import Invoice, Payment

    paid_total = invoice.payments.filter(
        status=Payment.Status.COMPLETED
    ).aggregate(total=_sum("amount"))["total"] or Decimal("0")

    if invoice.status == Invoice.Status.CANCELLED:
        return
    if paid_total <= 0:
        invoice.status = Invoice.Status.UNPAID
    elif paid_total < invoice.total_amount:
        invoice.status = Invoice.Status.PARTIALLY_PAID
    else:
        invoice.status = Invoice.Status.PAID
    invoice.save(update_fields=["status"])


def _sum(field_name):
    from django.db.models import Sum
    return Sum(field_name)


def record_payment(
    *,
    invoice,
    amount: Decimal,
    payment_method: str,
    payment_date,
    received_by: User,
    payer_name: str = "",
    gateway_reference: str = "",
    notes: str = "",
    request: HttpRequest | None = None,
):
    """Spec §19 'Payments', 'Partial payments'. Rejects a payment that
    would push the invoice's paid total over its `total_amount` — this
    MVP treats overpayment as an input error rather than silently
    creating a credit balance; revisit if your fee policy needs credits.

    On success: updates invoice.status (§19 'Balances'), auto-generates
    a Receipt (spec requires receipts to exist for payments), and writes
    an AuditLog entry (spec §19 'All financial modifications must be
    audited')."""
    from .models import Invoice, Payment, Receipt

    if amount <= 0:
        raise ValueError("Payment amount must be positive.")

    already_paid = invoice.payments.filter(
        status=Payment.Status.COMPLETED
    ).aggregate(total=_sum("amount"))["total"] or Decimal("0")

    if already_paid + amount > invoice.total_amount:
        raise ValueError(
            f"Payment of {amount} would exceed the invoice's remaining "
            f"balance of {invoice.total_amount - already_paid}."
        )

    payment = Payment.objects.create(
        invoice=invoice, amount=amount, payment_method=payment_method,
        payment_date=payment_date, received_by=received_by,
        payer_name=payer_name, gateway_reference=gateway_reference, notes=notes,
        status=Payment.Status.COMPLETED,
    )
    Receipt.objects.create(payment=payment, issued_by=received_by)
    _recompute_invoice_status(invoice)

    log_audit(
        actor=received_by, action=AuditLog.Action.CREATE, request=request,
        target_model="Payment", target_object_id=payment.pk,
        description=f"Recorded payment {payment.payment_number} against {invoice.invoice_number}",
        new_value={"amount": str(amount), "method": payment_method},
    )
    return payment


def request_refund(
    *, payment, amount: Decimal, reason: str, requested_by: User,
    request: HttpRequest | None = None,
):
    """Spec §19 'Refunds' — always a new record against the Payment, never
    an edit/deletion of the Payment itself (spec §38)."""
    from .models import Refund

    if amount > payment.amount:
        raise ValueError("Refund amount cannot exceed the original payment amount.")

    refund = Refund.objects.create(
        payment=payment, amount=amount, reason=reason, requested_by=requested_by,
    )
    log_audit(
        actor=requested_by, action=AuditLog.Action.CREATE, request=request,
        target_model="Refund", target_object_id=refund.pk,
        description=f"Requested refund {refund.refund_number} for {payment.payment_number}",
        new_value={"amount": str(amount), "reason": reason},
    )
    return refund


def decide_refund(
    *,
    refund,
    approve: bool,
    decided_by: User,
    refund_method: str = "",
    reference_number: str = "",
    request: HttpRequest | None = None,
):
    """Approving a refund reverses the underlying payment's contribution
    to the invoice's paid total (by recomputing invoice status from
    scratch, since Payment.status stays COMPLETED — the refund is tracked
    separately rather than mutating the original payment record, per
    spec §38)."""
    from .models import Refund

    if refund.status != Refund.Status.REQUESTED:
        raise ValueError(f"Refund is already {refund.status}; cannot decide it again.")

    from django.utils import timezone

    previous_status = refund.status
    refund.decided_by = decided_by
    refund.decided_at = timezone.now()

    if approve:
        refund.status = Refund.Status.APPROVED
        refund.refund_method = refund_method
        refund.reference_number = reference_number
        refund.save()
        refund.status = Refund.Status.COMPLETED
        refund.save(update_fields=["status"])
        _recompute_invoice_status_after_refund(refund)
    else:
        refund.status = Refund.Status.REJECTED
        refund.save()

    log_audit(
        actor=decided_by, action=AuditLog.Action.APPROVE if approve else AuditLog.Action.OTHER,
        request=request, target_model="Refund", target_object_id=refund.pk,
        description=f"{'Approved' if approve else 'Rejected'} refund {refund.refund_number}",
        previous_value={"status": previous_status}, new_value={"status": refund.status},
    )
    return refund


def _recompute_invoice_status_after_refund(refund) -> None:
    """A completed refund effectively reduces what's been paid against the
    invoice. Since Payment rows stay immutable, status is recomputed as
    (sum of completed payments) - (sum of completed refunds on those
    payments) compared to total_amount."""
    from .models import Invoice, Payment, Refund

    invoice = refund.payment.invoice
    paid_total = invoice.payments.filter(
        status=Payment.Status.COMPLETED
    ).aggregate(total=_sum("amount"))["total"] or Decimal("0")
    refunded_total = Refund.objects.filter(
        payment__invoice=invoice, status=Refund.Status.COMPLETED
    ).aggregate(total=_sum("amount"))["total"] or Decimal("0")

    net_paid = paid_total - refunded_total
    if invoice.status == Invoice.Status.CANCELLED:
        return
    if net_paid <= 0:
        invoice.status = Invoice.Status.UNPAID
    elif net_paid < invoice.total_amount:
        invoice.status = Invoice.Status.PARTIALLY_PAID
    else:
        invoice.status = Invoice.Status.PAID
    invoice.save(update_fields=["status"])


def apply_financial_adjustment(
    *, invoice, adjustment_type: str, amount: Decimal, reason: str,
    created_by: User, request: HttpRequest | None = None,
):
    """Spec §19 'Adjustments'. A signed correction to what's owed, applied
    as a new record rather than editing the invoice's original line
    items — spec §38 'prefer correction over destructive deletion'."""
    from .models import FinancialAdjustment

    adjustment = FinancialAdjustment.objects.create(
        invoice=invoice, adjustment_type=adjustment_type, amount=amount,
        reason=reason, created_by=created_by,
    )
    invoice.total_amount = invoice.total_amount + amount
    invoice.save(update_fields=["total_amount"])
    _recompute_invoice_status(invoice)

    log_audit(
        actor=created_by, action=AuditLog.Action.UPDATE, request=request,
        target_model="Invoice", target_object_id=invoice.pk,
        description=f"Applied {adjustment_type} of {amount} to {invoice.invoice_number}",
        new_value={"adjustment_amount": str(amount), "new_total": str(invoice.total_amount)},
    )
    return adjustment


def compute_student_account_summary(*, student: Student) -> dict[str, Any]:
    """Spec §19 'Student accounts', 'Balances', 'Arrears'. Aggregates
    across every invoice for the student — deliberately returns only
    financial totals, no academic data (spec: 'Do not expose academic
    grades to Finance Admin')."""
    from django.utils import timezone

    from .models import Invoice, Payment

    invoices = Invoice.objects.filter(student=student).exclude(
        status=Invoice.Status.CANCELLED
    )
    total_billed = invoices.aggregate(total=_sum("total_amount"))["total"] or Decimal("0")

    total_paid = Payment.objects.filter(
        invoice__student=student, status=Payment.Status.COMPLETED
    ).aggregate(total=_sum("amount"))["total"] or Decimal("0")

    today = timezone.localtime(timezone.now()).date()
    arrears = invoices.filter(
        due_date__lt=today
    ).exclude(status=Invoice.Status.PAID).aggregate(
        total=_sum("total_amount")
    )["total"] or Decimal("0")

    return {
        "total_billed": total_billed,
        "total_paid": total_paid,
        "outstanding_balance": total_billed - total_paid,
        "arrears": arrears,
    }


# =============================================================================
# Phase 13 — Library Module (spec §20)
# =============================================================================

def borrow_book(
    *,
    book_copy,
    student: Student | None = None,
    staff=None,
    issued_by,
    request: HttpRequest | None = None,
):
    """Spec §20 'Borrowing', 'Due dates'. Exactly one of student/staff must
    be given (enforced again here, not just by the DB constraint, so the
    error message is clear before hitting the database). Blocks borrowing
    if the copy isn't AVAILABLE, or if a student is already at their
    school's configured book limit."""
    from django.utils import timezone

    from .models import BookCopy, Borrowing, LibrarySettings

    if bool(student) == bool(staff):
        raise ValueError("Exactly one of student or staff must be provided.")

    if book_copy.status != BookCopy.Status.AVAILABLE:
        raise ValueError(f"'{book_copy}' is not available (status: {book_copy.status}).")

    school = student.school if student else staff.school
    settings_row, _ = LibrarySettings.objects.get_or_create(school=school)

    if student is not None:
        active_count = Borrowing.objects.filter(
            student=student, status=Borrowing.Status.BORROWED
        ).count()
        if active_count >= settings_row.max_books_per_student:
            raise ValueError(
                f"{student} has reached the maximum of "
                f"{settings_row.max_books_per_student} borrowed books."
            )

    today = timezone.localtime(timezone.now()).date()
    borrowing = Borrowing.objects.create(
        book_copy=book_copy, student=student, staff=staff, issued_by=issued_by,
        borrowed_date=today,
        due_date=today + datetime.timedelta(days=settings_row.loan_period_days),
    )
    book_copy.status = BookCopy.Status.BORROWED
    book_copy.save(update_fields=["status"])

    log_audit(
        actor=issued_by.user if hasattr(issued_by, "user") else issued_by,
        action=AuditLog.Action.CREATE, request=request,
        target_model="Borrowing", target_object_id=borrowing.pk,
        description=f"Issued '{book_copy}' to {student or staff}",
    )
    return borrowing


def return_book(
    *, borrowing, returned_to, request: HttpRequest | None = None,
):
    """Spec §20 'Returns', 'Fines'. Computes a fine from
    LibrarySettings.fine_per_day if returned after the due date; the copy
    goes back to AVAILABLE so it can be lent again."""
    from django.utils import timezone

    from .models import BookCopy, Borrowing, LibrarySettings

    if borrowing.status != Borrowing.Status.BORROWED:
        raise ValueError(f"This borrowing is already {borrowing.status}, cannot return it.")

    today = timezone.localtime(timezone.now()).date()
    borrower_school = borrowing.student.school if borrowing.student else borrowing.staff.school
    settings_row, _ = LibrarySettings.objects.get_or_create(school=borrower_school)

    days_late = max((today - borrowing.due_date).days, 0)
    fine = (Decimal(days_late) * settings_row.fine_per_day).quantize(Decimal("0.01"))

    borrowing.returned_date = today
    borrowing.status = Borrowing.Status.RETURNED
    borrowing.fine_amount = fine
    borrowing.returned_to = returned_to
    borrowing.save()

    borrowing.book_copy.status = BookCopy.Status.AVAILABLE
    borrowing.book_copy.save(update_fields=["status"])

    log_audit(
        actor=returned_to.user if hasattr(returned_to, "user") else returned_to,
        action=AuditLog.Action.UPDATE, request=request,
        target_model="Borrowing", target_object_id=borrowing.pk,
        description=f"Returned '{borrowing.book_copy}'"
        + (f" (fine: {fine})" if fine > 0 else ""),
    )
    return borrowing


def mark_book_lost(*, borrowing, marked_by, request: HttpRequest | None = None):
    """Spec §20 'Fines' implicitly covers loss too — a lost copy is removed
    from circulation (BookCopy.status -> LOST) rather than silently staying
    AVAILABLE or BORROWED forever."""
    from .models import BookCopy, Borrowing

    if borrowing.status != Borrowing.Status.BORROWED:
        raise ValueError(f"This borrowing is already {borrowing.status}.")

    borrowing.status = Borrowing.Status.LOST
    borrowing.save(update_fields=["status"])

    borrowing.book_copy.status = BookCopy.Status.LOST
    borrowing.book_copy.save(update_fields=["status"])

    log_audit(
        actor=marked_by.user if hasattr(marked_by, "user") else marked_by,
        action=AuditLog.Action.UPDATE, request=request,
        target_model="Borrowing", target_object_id=borrowing.pk,
        description=f"Marked '{borrowing.book_copy}' as lost",
    )
    return borrowing


def pay_library_fine(
    *, borrowing, amount_paid: Decimal, received_by, request: HttpRequest | None = None,
):
    """Standalone within the library module rather than routed through the
    Finance module's Payment/Invoice models — keeps library fines simple
    to record at the circulation desk. Revisit if the school wants unified
    billing across fees and library fines."""
    if amount_paid < borrowing.fine_amount:
        raise ValueError(
            f"Amount paid ({amount_paid}) is less than the fine owed "
            f"({borrowing.fine_amount})."
        )

    borrowing.fine_paid = True
    borrowing.save(update_fields=["fine_paid"])

    log_audit(
        actor=received_by.user if hasattr(received_by, "user") else received_by,
        action=AuditLog.Action.OTHER, request=request,
        target_model="Borrowing", target_object_id=borrowing.pk,
        description=f"Library fine of {borrowing.fine_amount} paid",
    )
    return borrowing


# =============================================================================
# Phase 14 — Timetable Module (spec §21)
# =============================================================================

def create_timetable_slot(
    *,
    teaching_assignment,
    day_of_week: str,
    period,
    room=None,
    request: HttpRequest | None = None,
):
    """Spec §21 'Prevent scheduling conflicts where possible. Detect:
    Teacher double-booking, Room double-booking, Class double-booking'.

    Runs explicit pre-checks first so the error message says exactly
    which of the three conflict types was hit (a raw IntegrityError from
    the composite DB constraints — see TimetableSlot.Meta — wouldn't
    distinguish between them). The DB constraints remain as a second,
    unconditional line of defense against races/direct DB writes.
    """
    from .models import TimetableSlot

    term = teaching_assignment.term
    teacher = teaching_assignment.teacher
    class_group = teaching_assignment.class_subject.class_group

    if TimetableSlot.objects.filter(
        teacher=teacher, term=term, day_of_week=day_of_week, period=period
    ).exists():
        raise ValueError(
            f"{teacher} already has a lesson scheduled on "
            f"{day_of_week} during {period}."
        )

    if room is not None and TimetableSlot.objects.filter(
        room=room, term=term, day_of_week=day_of_week, period=period
    ).exists():
        raise ValueError(f"{room} is already booked on {day_of_week} during {period}.")

    if TimetableSlot.objects.filter(
        class_group=class_group, term=term, day_of_week=day_of_week, period=period
    ).exists():
        raise ValueError(
            f"{class_group} already has a lesson scheduled on "
            f"{day_of_week} during {period}."
        )

    slot = TimetableSlot.objects.create(
        teaching_assignment=teaching_assignment, room=room,
        day_of_week=day_of_week, period=period,
    )

    log_audit(
        actor=teacher.user, action=AuditLog.Action.CREATE, request=request,
        target_model="TimetableSlot", target_object_id=slot.pk,
        description=f"Scheduled {slot}",
    )
    return slot


def reschedule_timetable_slot(
    *, slot, day_of_week: str = None, period=None, room=None,
    changed_by, request: HttpRequest | None = None,
):
    """Moving a slot re-runs the same three conflict checks against the
    new day/period/room before committing — implemented as delete-then-
    recreate via create_timetable_slot() so the checks and audit trail
    stay in exactly one place rather than duplicating validation logic.
    Wrapped in transaction.atomic() so a failed reschedule can never leave
    the timetable with neither the old nor the new slot — either the
    move fully succeeds, or the original slot is left exactly as it was."""
    from django.db import transaction

    from .models import TimetableSlot

    new_day = day_of_week if day_of_week is not None else slot.day_of_week
    new_period = period if period is not None else slot.period
    new_room = room if room is not None else slot.room

    teaching_assignment = slot.teaching_assignment
    old_description = str(slot)

    with transaction.atomic():
        slot.delete()
        try:
            new_slot = create_timetable_slot(
                teaching_assignment=teaching_assignment, day_of_week=new_day,
                period=new_period, room=new_room, request=request,
            )
        except ValueError:
            # Raising inside the atomic block rolls back the delete()
            # automatically — the original slot's row is restored exactly
            # as it was, no manual re-create needed.
            raise

    log_audit(
        actor=changed_by, action=AuditLog.Action.UPDATE, request=request,
        target_model="TimetableSlot", target_object_id=new_slot.pk,
        description=f"Rescheduled '{old_description}' -> '{new_slot}'",
    )
    return new_slot


# =============================================================================
# Phase 15 — Communication Module (spec §22)
#
# Channel honesty: EMAIL works for real right now, using Django's
# configured mail backend (console backend in dev, SMTP in prod — set up
# since Phase 1). SMS and PUSH are architected — Channel choices,
# per-delivery status tracking, NotificationPreference opt-in — but not
# wired to a live provider. Sending to those channels marks the delivery
# FAILED with a clear "gateway not configured" message rather than
# pretending to succeed; wiring a real provider (e.g. Africa's Talking or
# Twilio for SMS, FCM/APNs for push) needs real credentials from the
# school and should be a dedicated follow-up, not fabricated here.
# =============================================================================

def _deliver_email(*, notification, delivery) -> None:
    from django.core.mail import send_mail
    from django.utils import timezone

    recipient_email = notification.recipient.email
    if not recipient_email:
        delivery.status = delivery.Status.FAILED
        delivery.error_message = "Recipient has no email address on file."
        delivery.save(update_fields=["status", "error_message"])
        return

    try:
        send_mail(
            subject=notification.title, message=notification.message,
            from_email=None,  # uses DEFAULT_FROM_EMAIL
            recipient_list=[recipient_email], fail_silently=False,
        )
        delivery.status = delivery.Status.SENT
        delivery.sent_at = timezone.now()
        delivery.save(update_fields=["status", "sent_at"])
    except Exception as exc:  # noqa: BLE001 — any backend failure must be recorded, not raised
        delivery.status = delivery.Status.FAILED
        delivery.error_message = str(exc)[:255]
        delivery.save(update_fields=["status", "error_message"])


def _deliver_sms(*, notification, delivery) -> None:
    """No live SMS gateway is configured. Marks the attempt FAILED with an
    honest reason rather than silently no-op'ing as if it succeeded."""
    delivery.status = delivery.Status.FAILED
    delivery.error_message = "SMS gateway not configured."
    delivery.save(update_fields=["status", "error_message"])


def _deliver_push(*, notification, delivery) -> None:
    """No live push gateway (FCM/APNs) is configured — same honesty as
    _deliver_sms."""
    delivery.status = delivery.Status.FAILED
    delivery.error_message = "Push gateway not configured."
    delivery.save(update_fields=["status", "error_message"])


_CHANNEL_HANDLERS = {
    "EMAIL": _deliver_email,
    "SMS": _deliver_sms,
    "PUSH": _deliver_push,
}


def send_notification(
    *,
    recipient: User,
    notification_type: str,
    title: str,
    body: str,
    channels: list[str] | None = None,
    related_model: str = "",
    related_object_id: str | int = "",
    request: HttpRequest | None = None,
):
    """Spec §22: role-aware in-app/email/SMS/push notifications.

    An in-app Notification row is always created (zero-cost, no gateway
    needed). Additional channels are attempted only if both requested
    AND the recipient's NotificationPreference has that channel enabled
    — a recipient who has opted out of email never gets an email attempt
    logged as failed, they simply don't get one.
    """
    from .models import Notification, NotificationDelivery, NotificationPreference

    prefs, _ = NotificationPreference.objects.get_or_create(user=recipient)

    notification = Notification.objects.create(
        recipient=recipient, notification_type=notification_type,
        title=title, message=body, related_model=related_model,
        related_object_id=str(related_object_id) if related_object_id != "" else "",
    )

    # In-app is implicit/free — record it as SENT immediately, no dispatch needed.
    if prefs.in_app_enabled:
        NotificationDelivery.objects.create(
            notification=notification, channel=NotificationDelivery.Channel.IN_APP,
            status=NotificationDelivery.Status.SENT,
        )

    channel_enabled = {
        "EMAIL": prefs.email_enabled, "SMS": prefs.sms_enabled, "PUSH": prefs.push_enabled,
    }
    for channel in (channels or []):
        if channel not in _CHANNEL_HANDLERS:
            continue
        if not channel_enabled.get(channel, False):
            continue
        delivery = NotificationDelivery.objects.create(
            notification=notification, channel=channel,
            status=NotificationDelivery.Status.PENDING,
        )
        _CHANNEL_HANDLERS[channel](notification=notification, delivery=delivery)

    return notification


def create_announcement(
    *, school, title: str, body: str, audience: str, created_by: User,
    channels: list[str] | None = None, request: HttpRequest | None = None,
):
    """Spec §22 'Announcements', role-aware fan-out. Creates the
    Announcement record, then a Notification for every matching recipient
    — matching the same per-recipient-row design as send_notification()
    so each person's read state is independent."""
    from django.utils import timezone

    from .models import Announcement, Notification, School as SchoolModel

    announcement = Announcement.objects.create(
        school=school, title=title, body=body, audience=audience,
        created_by=created_by, published_at=timezone.now(),
    )

    recipients = _resolve_announcement_audience(school=school, audience=audience)
    for recipient in recipients:
        send_notification(
            recipient=recipient, notification_type=Notification.NotificationType.ANNOUNCEMENT,
            title=title, body=body, channels=channels,
            related_model="Announcement", related_object_id=announcement.pk, request=request,
        )

    log_audit(
        actor=created_by, action=AuditLog.Action.CREATE, request=request,
        target_model="Announcement", target_object_id=announcement.pk,
        description=f"Published announcement '{title}' to {audience}",
    )
    return announcement


def _resolve_announcement_audience(*, school, audience: str):
    from .models import Announcement

    role_map = {
        Announcement.Audience.STUDENTS: [User.Role.STUDENT],
        Announcement.Audience.PARENTS: [User.Role.PARENT],
        Announcement.Audience.TEACHERS: [User.Role.TEACHER, User.Role.CLASS_TEACHER],
        Announcement.Audience.STAFF: [
            User.Role.STAFF_ADMIN, User.Role.ACADEMIC_ADMIN, User.Role.FINANCE_ADMIN,
            User.Role.TEACHER, User.Role.EXAM_OFFICER, User.Role.CLASS_TEACHER,
            User.Role.DEPARTMENT_HEAD, User.Role.ACCOUNTANT, User.Role.LIBRARIAN,
        ],
    }
    if audience == Announcement.Audience.ALL:
        return User.objects.filter(is_active=True).filter(_in_school_q(school))
    roles = role_map.get(audience, [])
    return User.objects.filter(is_active=True, role__in=roles).filter(_in_school_q(school))


def _in_school_q(school):
    """Users don't have a direct `school` FK (only Student/Staff/Guardian
    do); this maps a User back to their school through whichever profile
    they have, or matches everyone if the school can't be determined
    (e.g. a bare superuser account with no Student/Staff profile)."""
    from django.db.models import Q

    return (
        Q(student_profile__school=school)
        | Q(staff_profile__school=school)
        | Q(guardian_profile__school=school)
        | Q(is_superuser=True)
    )


def mark_notification_read(*, notification) -> None:
    from django.utils import timezone

    if not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at"])
        from django.core.cache import cache
        cache.delete(f"dashboard-notifications:{notification.recipient_id}")


# --- Role-aware convenience wrappers matching spec §22's exact examples ---

def notify_result_published(*, student: Student, subject_name: str, request: HttpRequest | None = None):
    """Spec §22 example: Student — "Your Mathematics results have been published." """
    return send_notification(
        recipient=student.user,
        notification_type="RESULT_PUBLISHED",
        title="Results Published",
        body=f"Your {subject_name} results have been published.",
        channels=["EMAIL"], request=request,
    )


def notify_report_available(*, guardian, student: Student, term, request: HttpRequest | None = None):
    """Spec §22 example: Parent — "Your child's Term 2 report is available."
    No-ops if the guardian has no linked portal account yet (Guardian.user
    is optional — see Phase 3) since there's no User to notify."""
    if guardian.user_id is None:
        return None
    return send_notification(
        recipient=guardian.user,
        notification_type="REPORT_AVAILABLE",
        title="Report Available",
        body=f"Your child's {term} report is available.",
        channels=["EMAIL"], request=request,
    )


def notify_payment_received(
    *, recipient: User, amount: Decimal, invoice_number: str, request: HttpRequest | None = None,
):
    """Spec §22 example: Finance — "Payment received." """
    return send_notification(
        recipient=recipient,
        notification_type="PAYMENT_RECEIVED",
        title="Payment Received",
        body=f"Payment of {amount} received for invoice {invoice_number}.",
        channels=["EMAIL"], request=request,
    )


def notify_assignment_deadline_approaching(
    *, teacher_user: User, assignment_title: str, due_date, request: HttpRequest | None = None,
):
    """Spec §22 example: Teacher — "Assignment deadline approaching." """
    return send_notification(
        recipient=teacher_user,
        notification_type="ASSIGNMENT_DEADLINE",
        title="Assignment Deadline Approaching",
        body=f"'{assignment_title}' is due on {due_date}.",
        channels=["EMAIL"], request=request,
    )


# =============================================================================
# Phase 17 — Parent/Guardian Portal (spec §18)
# =============================================================================

def get_children_for_guardian(*, guardian_user: User):
    """Spec §18 'Parent -> Child 1/2/3' -- a parent can have multiple
    children, modeled via the existing StudentGuardian through-table
    (Phase 3), not a new relation. Returns every Student linked to this
    guardian's portal account, ordered for a stable dashboard listing."""
    from .models import Guardian, Student

    guardian = Guardian.objects.filter(user=guardian_user).first()
    if guardian is None:
        return Student.objects.none()
    return Student.objects.filter(
        studentguardian__guardian=guardian
    ).distinct().order_by("admission_number")


# =============================================================================
# Phase 18 — Teacher Dashboard (spec §9)
# =============================================================================

def mark_attendance(
    *,
    class_subject,
    term,
    date,
    taken_by,
    records: dict[int, dict],
    request: HttpRequest | None = None,
):
    """Spec §9/§11: a teacher's INITIAL attendance submission for one
    class+subject+date — distinct from correct_attendance_record()
    (Phase 6), which is Academic Admin's after-the-fact correction path.

    `records` maps student_id -> {"status": ..., "notes": ...}. Blocks
    re-marking a session Academic Admin has already locked (spec §11
    'Academic Admin can... correct attendance with appropriate
    permissions' implies the teacher's window to freely re-submit ends
    once that review has happened)."""
    from .models import AttendanceRecord, AttendanceSession

    session, created = AttendanceSession.objects.get_or_create(
        class_subject=class_subject, date=date,
        defaults={"term": term, "taken_by": taken_by},
    )
    if session.is_locked:
        raise ValueError(
            "This attendance session has been locked by Academic Admin; "
            "use the correction workflow instead of re-marking it."
        )
    if not created:
        session.taken_by = taken_by
        session.save(update_fields=["taken_by"])

    for student_id, payload in records.items():
        AttendanceRecord.objects.update_or_create(
            session=session, student_id=student_id,
            defaults={
                "status": payload["status"],
                "notes": payload.get("notes", ""),
                "recorded_by": taken_by.user,
            },
        )

    log_audit(
        actor=taken_by.user, action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        request=request, target_model="AttendanceSession", target_object_id=session.pk,
        description=f"Marked attendance for {class_subject} on {date}",
    )
    return session


def record_assessment_marks(
    *,
    assessment: "Assessment",
    teacher,
    marks: dict[int, Decimal],
    request: HttpRequest | None = None,
):
    """Spec §9 'Enter marks'. Only allowed while the assessment is still
    in a teacher-editable stage (DRAFT or REJECTED) — once submitted, the
    result-processing workflow (Phase 8) owns further changes, and once
    published, AssessmentMark.save() itself refuses direct edits (spec
    §14, enforced at the model layer since Phase 8)."""
    from .models import Assessment as AssessmentModel, AssessmentMark

    editable_statuses = {
        AssessmentModel.WorkflowStatus.DRAFT, AssessmentModel.WorkflowStatus.REJECTED,
    }
    if assessment.workflow_status not in editable_statuses:
        raise ValueError(
            f"Marks cannot be entered while this assessment is "
            f"'{assessment.workflow_status}'."
        )

    updated = []
    for student_id, mark_value in marks.items():
        if mark_value > assessment.component.max_marks:
            raise ValueError(
                f"Mark {mark_value} exceeds this component's max_marks "
                f"({assessment.component.max_marks})."
            )
        record, _ = AssessmentMark.objects.update_or_create(
            assessment=assessment, student_id=student_id,
            defaults={"marks_obtained": mark_value, "recorded_by": teacher.user},
        )
        updated.append(record)

    log_audit(
        actor=teacher.user, action=AuditLog.Action.UPDATE, request=request,
        target_model="Assessment", target_object_id=assessment.pk,
        description=f"Entered/updated {len(updated)} mark(s) for {assessment}",
    )
    return updated


# =============================================================================
# Phase 19 — Finance Admin Dashboard (spec §19, §23)
# =============================================================================

def compute_school_financial_summary(*, school) -> dict[str, Any]:
    """School-wide equivalent of compute_student_account_summary() — same
    data-minimization rule applies (financial totals only, no academic
    joins). Used by the Finance Admin overview page."""
    from django.utils import timezone

    from .models import Invoice, Payment

    invoices = Invoice.objects.filter(school=school).exclude(status=Invoice.Status.CANCELLED)
    total_billed = invoices.aggregate(total=_sum("total_amount"))["total"] or Decimal("0")

    total_collected = Payment.objects.filter(
        invoice__school=school, status=Payment.Status.COMPLETED
    ).aggregate(total=_sum("amount"))["total"] or Decimal("0")

    today = timezone.localtime(timezone.now()).date()
    overdue_invoices = invoices.filter(due_date__lt=today).exclude(status=Invoice.Status.PAID)
    arrears = overdue_invoices.aggregate(total=_sum("total_amount"))["total"] or Decimal("0")

    return {
        "total_billed": total_billed,
        "total_collected": total_collected,
        "outstanding_balance": total_billed - total_collected,
        "arrears": arrears,
        "overdue_invoice_count": overdue_invoices.count(),
        "unpaid_invoice_count": invoices.filter(status=Invoice.Status.UNPAID).count(),
    }


# =============================================================================
# Phase 20 — Staff Admin Dashboard (spec §6)
# =============================================================================

def record_staff_attendance(
    *,
    staff: Staff,
    date,
    status: str,
    recorded_by: User,
    check_in_time=None,
    check_out_time=None,
    notes: str = "",
    request: HttpRequest | None = None,
):
    """Spec §6 'Staff attendance'. update_or_create so re-marking the same
    staff/date corrects the existing row rather than erroring — mirrors
    the Student attendance pattern (Phase 6) but one row per day, not per
    class period."""
    from .models import StaffAttendanceRecord

    record, created = StaffAttendanceRecord.objects.update_or_create(
        staff=staff, date=date,
        defaults={
            "status": status, "check_in_time": check_in_time,
            "check_out_time": check_out_time, "notes": notes, "recorded_by": recorded_by,
        },
    )
    log_audit(
        actor=recorded_by, action=AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE,
        request=request, target_model="StaffAttendanceRecord", target_object_id=record.pk,
        description=f"{'Recorded' if created else 'Updated'} attendance for {staff} on {date}: {status}",
    )
    return record


def submit_leave_request(
    *,
    staff: Staff,
    leave_type: str,
    start_date,
    end_date,
    reason: str = "",
    request: HttpRequest | None = None,
):
    """Spec §6 'Staff submits leave request.' First step of the workflow."""
    from .models import LeaveRequest

    if end_date < start_date:
        raise ValueError("Leave end date cannot be before the start date.")

    leave_request = LeaveRequest.objects.create(
        staff=staff, leave_type=leave_type, start_date=start_date,
        end_date=end_date, reason=reason,
    )
    log_audit(
        actor=staff.user, action=AuditLog.Action.CREATE, request=request,
        target_model="LeaveRequest", target_object_id=leave_request.pk,
        description=f"{staff} requested {leave_type} leave ({start_date} to {end_date})",
    )
    return leave_request


def decide_leave_request(
    *,
    leave_request,
    approve: bool,
    decided_by: User,
    decision_notes: str = "",
    request: HttpRequest | None = None,
):
    """Spec §6 'Staff Admin reviews. Staff Admin approves/rejects. System
    records decision. User receives notification.' The notification step
    is not optional in the spec — wired here via send_notification()
    (Phase 15) rather than left as a TODO."""
    from django.utils import timezone

    from .models import LeaveRequest

    if leave_request.status != LeaveRequest.Status.PENDING:
        raise ValueError(f"This leave request is already {leave_request.status}.")

    leave_request.status = (
        LeaveRequest.Status.APPROVED if approve else LeaveRequest.Status.REJECTED
    )
    leave_request.reviewed_by = decided_by
    leave_request.reviewed_at = timezone.now()
    leave_request.decision_notes = decision_notes
    leave_request.save()

    send_notification(
        recipient=leave_request.staff.user,
        notification_type="OTHER",
        title=f"Leave Request {leave_request.get_status_display()}",
        body=(
            f"Your {leave_request.get_leave_type_display()} request "
            f"({leave_request.start_date} to {leave_request.end_date}) "
            f"was {leave_request.get_status_display().lower()}."
            + (f" Note: {decision_notes}" if decision_notes else "")
        ),
        channels=["EMAIL"], request=request,
    )

    log_audit(
        actor=decided_by, action=AuditLog.Action.APPROVE if approve else AuditLog.Action.OTHER,
        request=request, target_model="LeaveRequest", target_object_id=leave_request.pk,
        description=f"{'Approved' if approve else 'Rejected'} leave request for {leave_request.staff}",
    )
    return leave_request


def deactivate_staff(
    *, staff: Staff, deactivated_by: User, reason: str = "", request: HttpRequest | None = None,
):
    """Spec §6 'Deactivate staff'. Deactivates both the Staff profile and
    the linked login (User.is_active) — a deactivated staff member
    shouldn't still be able to log in. Never deletes the row (spec
    §37/§38 'prefer correction over destructive deletion')."""
    previous_value = {"is_active": staff.is_active, "employment_status": staff.employment_status}

    staff.is_active = False
    staff.employment_status = staff.EmploymentStatus.TERMINATED
    staff.save(update_fields=["is_active", "employment_status"])
    staff.user.is_active = False
    staff.user.save(update_fields=["is_active"])

    log_audit(
        actor=deactivated_by, action=AuditLog.Action.UPDATE, request=request,
        target_model="Staff", target_object_id=staff.pk,
        description=f"Deactivated {staff}" + (f": {reason}" if reason else ""),
        previous_value=previous_value,
        new_value={"is_active": False, "employment_status": staff.employment_status},
    )
    return staff


def reactivate_staff(*, staff: Staff, reactivated_by: User, request: HttpRequest | None = None):
    """Reverses deactivate_staff() — a correction, not a new capability,
    consistent with spec §37/§38's 'never silently destroy, always
    provide correction paths' principle."""
    staff.is_active = True
    staff.employment_status = staff.EmploymentStatus.ACTIVE
    staff.save(update_fields=["is_active", "employment_status"])
    staff.user.is_active = True
    staff.user.save(update_fields=["is_active"])

    log_audit(
        actor=reactivated_by, action=AuditLog.Action.UPDATE, request=request,
        target_model="Staff", target_object_id=staff.pk,
        description=f"Reactivated {staff}",
    )
    return staff


def compute_staff_workload(*, staff: Staff, term) -> dict[str, Any]:
    """Spec §6 'Staff workload': assigned classes, assigned subjects,
    teaching hours, timetable. Teaching hours are derived from actual
    scheduled TimetableSlots (Phase 14), not just counted
    TeachingAssignments, so a subject taught 5x/week correctly counts
    more than one taught once."""
    from .models import TeachingAssignment, TimetableSlot

    assignments = TeachingAssignment.objects.filter(
        teacher=staff, term=term, is_active=True
    ).select_related("class_subject__class_group", "class_subject__subject")

    slots = TimetableSlot.objects.filter(
        teacher=staff, term=term
    ).select_related("period")

    total_minutes = 0
    for slot in slots:
        start = slot.period.start_time
        end = slot.period.end_time
        total_minutes += (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)

    return {
        "assigned_classes": assignments.values_list(
            "class_subject__class_group__name", flat=True
        ).distinct().count(),
        "assigned_subjects": assignments.values_list(
            "class_subject__subject__name", flat=True
        ).distinct().count(),
        "weekly_periods": slots.count(),
        "weekly_teaching_hours": round(total_minutes / 60, 1),
        "assignments": assignments,
        "slots": slots,
    }


# =============================================================================
# Phase 21 — Academic Admin Dashboard (spec §7).
# 'Academic Admin must manage academic operations without accessing
# confidential financial information' — every function here works with
# Student/Enrollment/Assessment/AttendanceRecord only; nothing in this
# section imports or queries Invoice/Payment/Refund/FeeStructure.
# =============================================================================

def register_student(
    *,
    school,
    username: str,
    password: str | None,
    first_name: str,
    last_name: str,
    email: str,
    admission_number: str,
    admission_date,
    current_class=None,
    current_stream=None,
    program=None,
    registered_by: User,
    request: HttpRequest | None = None,
):
    """Spec §7 'Student registration', 'Admission numbers'. Creates the
    login (User) and academic profile (Student) together — a Student
    cannot exist without a User, per the OneToOneField in Phase 3."""
    from django.utils.crypto import get_random_string

    from .models import Student

    new_user = User.objects.create_user(
        username=username, password=password or get_random_string(16),
        first_name=first_name, last_name=last_name, email=email,
        role=User.Role.STUDENT,
    )
    student = Student.objects.create(
        user=new_user, school=school, admission_number=admission_number,
        admission_date=admission_date, current_class=current_class,
        current_stream=current_stream, program=program,
    )
    log_audit(
        actor=registered_by, action=AuditLog.Action.CREATE, request=request,
        target_model="Student", target_object_id=student.pk,
        description=f"Registered student {student} ({admission_number})",
    )
    return student


def change_student_status(
    *, student: Student, new_status: str, changed_by: User,
    reason: str = "", request: HttpRequest | None = None,
):
    """Spec §7 'Student status': Active, Graduated, Suspended,
    Transferred, Deferred, Withdrawn, Expelled, Alumni. A status change
    is a correction event, not a silent field edit — always audit-logged
    with the before/after value (spec §37/§38)."""
    from .models import Student

    if new_status not in Student.Status.values:
        raise ValueError(f"'{new_status}' is not a valid student status.")

    previous_status = student.status
    student.status = new_status
    student.save(update_fields=["status"])

    log_audit(
        actor=changed_by, action=AuditLog.Action.UPDATE, request=request,
        target_model="Student", target_object_id=student.pk,
        description=f"Changed status of {student} from {previous_status} to {new_status}"
        + (f": {reason}" if reason else ""),
        previous_value={"status": previous_status}, new_value={"status": new_status},
    )
    return student


def compute_school_academic_summary(*, school) -> dict[str, Any]:
    """Overview page aggregate — student counts by status, pending
    assessment-approval queue depth, current academic year/term. No
    financial data anywhere in this function, per spec §7's constraint."""
    from .models import AcademicYear, Assessment, Student, Term

    students = Student.objects.filter(school=school)
    current_year = AcademicYear.objects.filter(school=school, is_current=True).first()
    current_term = Term.objects.filter(
        academic_year__school=school, is_current=True
    ).first()

    pending_statuses = [
        Assessment.WorkflowStatus.SUBMITTED, Assessment.WorkflowStatus.REVIEWED,
        Assessment.WorkflowStatus.VERIFIED,
    ]
    pending_approvals = Assessment.objects.filter(
        class_subject__class_group__school=school, workflow_status__in=pending_statuses
    ).count()

    return {
        "total_students": students.filter(is_active=True).count(),
        "status_breakdown": [
            {"code": status, "label": label, "count": students.filter(status=status).count()}
            for status, label in Student.Status.choices
        ],
        "current_academic_year": current_year,
        "current_term": current_term,
        "pending_result_approvals": pending_approvals,
    }


def compute_school_attendance_summary(*, school, days: int = 30) -> dict[str, Any]:
    """Spec §5 Super Admin overview 'Attendance Overview' card. A rolling
    window (default 30 days), not all-time — an all-time percentage
    across a school's whole history would be a nearly-meaningless number
    that never moves; a recent window is what actually tells an admin
    "is attendance currently healthy."""
    from django.utils import timezone

    from .models import AttendanceRecord

    today = timezone.localtime(timezone.now()).date()
    window_start = today - datetime.timedelta(days=days)

    records = AttendanceRecord.objects.filter(
        session__class_subject__class_group__school=school,
        session__date__gte=window_start, session__date__lte=today,
    )
    total = records.count()
    present = records.filter(status=AttendanceRecord.Status.PRESENT).count()

    return {
        "window_days": days,
        "total_records": total,
        "present_count": present,
        "attendance_rate_percent": (
            round((present / total) * 100, 1) if total > 0 else None
        ),
    }
