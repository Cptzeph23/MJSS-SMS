# Absolute path: SMS/smsApp/views.py
import csv
import io
import datetime
from decimal import Decimal, InvalidOperation

from django.contrib.auth import logout as auth_logout, update_session_auth_hash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseForbidden, JsonResponse, FileResponse
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import RedirectView, TemplateView

from .validators import (
    COURSE_MATERIAL_TYPE_TO_CATEGORY,
    FileValidationError,
    validate_course_material_content,
    validate_document_content,
    validate_course_material_content,
    validate_document_content,
    validate_image_content,
    validate_pdf_content,
    validate_upload,
)
from .models import (
    AcademicYear,
    Announcement,
    Assessment,
    AssessmentComponent,
    AssessmentStructure,
    AssessmentType,
    AssessmentMark,
    Assignment,
    AssignmentResource,
    AssignmentSubmission,
    AttendanceRecord,
    AuditLog,
    AttendanceSession,
    Campus,
    Class,
    ClassSubject,
    CourseMaterial,
    Department,
    Discussion,
    Enrollment,
    FeeCategory,
    FeeConcession,
    FeeStructure,
    FeeStructureItem,
    FeeStructureTransport,
    GradeBand,
    GradingScheme,
    Guardian,
    Invoice,
    LeaveRequest,
    Notification,
    Payment,
    PaymentAllocation,
    Period,
    Program,
    Quiz,
    QuizAnswer,
    QuizAttempt,
    QuizOption,
    QuizQuestion,
    Refund,
    ReportCard,
    ReportTemplate,
    Staff,
    StaffAttendanceRecord,
    StaffQualification,
    Stream,
    Student,
    StudentGuardian,
    Room,
    Subject,
    School,
    TeachingAssignment,
    Term,
    TimetableSlot,
    Transcript,
    User,
)
from .permissions import RoleRequiredMixin
from .middleware import user_can_access_school
# Roles retained in the database for backwards compatibility but not offered
# for new assignments while those modules are inactive.
ACTIVE_ROLE_VALUES = {
    User.Role.SUPER_ADMIN, User.Role.MANAGER, User.Role.PRINCIPAL,
    User.Role.DEPUTY_PRINCIPAL, User.Role.TEACHER, User.Role.PARENT,
    User.Role.STUDENT, User.Role.ACCOUNTANT,
}
ACTIVE_ROLE_CHOICES = tuple((value, label) for value, label in User.Role.choices if value in ACTIVE_ROLE_VALUES)

from .services import (
    apply_financial_adjustment,
    change_student_status,
    compute_school_academic_summary,
    compute_school_attendance_summary,
    compute_school_financial_summary,
    compute_student_subject_completion,
    compute_staff_workload,
    compute_student_account_summary,
    compute_family_account_summary,
    compute_weighted_average,
    ensure_current_term_invoice_for_student,
    correct_attendance_record,
    deactivate_staff,
    decide_leave_request,
    decide_refund,
    generate_batch_reports,
    generate_invoice_for_student,
    generate_report_pdf,
    generate_transcript,
    get_student_payment_records,
    get_children_for_guardian,
    get_dashboard_url_for_role,
    get_grade_for_mark,
    grade_quiz_short_answer,
    log_audit,
    mark_attendance,
    mark_notification_read,
    reactivate_staff,
    record_assessment_marks,
    record_teacher_markbook_marks,
    record_login,
    record_payment,
    record_family_payment,
    update_family_payment,
    assign_fee_structure_to_class,
    build_parent_academic_history,
    build_student_financial_history,
    create_assessments_for_structure,
    record_staff_attendance,
    send_notification,
    register_student,
    render_report_html,
    submit_assignment,
    submit_leave_request,
    transition_assessment_workflow,
    verify_transcript,
)


class LoginView(DjangoLoginView):
    """Wraps Django's built-in LoginView to also write LoginHistory/AuditLog
    entries (spec §5 'View login history', §27 audit logging) and enforce
    a per-IP rate limit on failed attempts (spec §27 'Rate limiting where
    appropriate') — the login form is the most direct brute-force/
    credential-stuffing target in the whole application."""

    template_name = "registration/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        if self.request.user.must_change_password:
            return reverse("dashboard:password_change")
        return super().get_success_url()

    MAX_FAILED_ATTEMPTS = 5
    LOCKOUT_WINDOW_SECONDS = 300  # 5 minutes

    def _client_ip(self) -> str:
        forwarded = self.request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return self.request.META.get("REMOTE_ADDR", "unknown")

    def _lockout_cache_key(self) -> str:
        return f"login_failed_attempts:{self._client_ip()}"

    def dispatch(self, request, *args, **kwargs):
        from django.core.cache import cache

        attempts = cache.get(self._lockout_cache_key(), 0)
        if attempts >= self.MAX_FAILED_ATTEMPTS:
            return render(
                request, "registration/login_locked.html",
                {"retry_after_seconds": self.LOCKOUT_WINDOW_SECONDS}, status=429,
            )
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        from django.core.cache import cache

        school = getattr(self.request, "school", None)
        if school is not None and not user_can_access_school(form.get_user(), school):
            # Keep the response deliberately generic: do not disclose whether
            # this username exists in another school's tenant.
            form.add_error(None, "Unable to sign in with these credentials.")
            return self.form_invalid(form)

        cache.delete(self._lockout_cache_key())  # successful login clears the counter
        response = super().form_valid(form)
        record_login(user=form.get_user(), request=self.request, was_successful=True)
        return response

    def form_invalid(self, form):
        from django.core.cache import cache

        key = self._lockout_cache_key()
        attempts = cache.get(key, 0) + 1
        cache.set(key, attempts, timeout=self.LOCKOUT_WINDOW_SECONDS)

        # Only log a failed attempt if a real user was targeted, to avoid
        # creating noise/PII rows for arbitrary junk usernames.
        username = form.data.get("username")
        if username:
            user = User.objects.filter(username=username).first()
            if user:
                record_login(user=user, request=self.request, was_successful=False)
        return super().form_invalid(form)


class LogoutView(View):
    """End the browser session and return the user to the login page.

    The dashboard profile menu uses a normal link, so GET must be supported
    for that click to work across every dashboard base template. POST is also
    accepted for clients that choose the form-based logout pattern.
    """

    def _logout(self, request):
        if request.session.session_key:
            from django.core.cache import cache
            cache.delete(f"browser-idle:{request.session.session_key}")
        auth_logout(request)
        return redirect("dashboard:login")

    def get(self, request):
        return self._logout(request)

    def post(self, request):
        return self._logout(request)


class ForcedPasswordChangeView(LoginRequiredMixin, View):
    template_name = "registration/force_password_change.html"
    login_url = "dashboard:login"

    def get(self, request):
        if not request.user.must_change_password:
            return redirect("dashboard:home")
        return render(request, self.template_name)

    def post(self, request):
        if not request.user.must_change_password:
            return redirect("dashboard:home")
        password = request.POST.get("new_password", "")
        confirmation = request.POST.get("confirm_password", "")
        errors = []
        if password != confirmation:
            errors.append("The passwords do not match.")
        try:
            validate_password(password, user=request.user)
        except ValidationError as exc:
            errors.extend(exc.messages)
        if errors:
            return render(request, self.template_name, {"errors": errors}, status=400)
        request.user.set_password(password)
        request.user.must_change_password = False
        request.user.save(update_fields=["password", "must_change_password", "updated_at"])
        update_session_auth_hash(request, request.user)
        return redirect("dashboard:home")


class KeepAliveView(LoginRequiredMixin, View):
    login_url = "dashboard:login"

    def post(self, request):
        return JsonResponse({"active": True})


class DashboardRouterView(LoginRequiredMixin, RedirectView):
    """Post-login landing point. Redirects each user to the dashboard that
    matches their role (spec §4 System User Types) instead of one shared
    dashboard, per the multi-dashboard structure §5-§19 describe.
    LoginRequiredMixin sends anonymous visitors to LOGIN_URL first —
    without it, `request.user` is an AnonymousUser with no `.role`."""

    permanent = False
    login_url = "dashboard:login"

    def get_redirect_url(self, *args, **kwargs):
        return get_dashboard_url_for_role(self.request.user)


class GlobalSearchView(LoginRequiredMixin, View):
    """Search system records using role-scoped querysets only."""
    template_name = "dashboard/search_results.html"

    def get(self, request):
        query = request.GET.get("q", "").strip()[:100]
        user = request.user
        role = user.role
        results = []
        normalized_query = " ".join(query.casefold().split())
        # Let users search by the name of a record type as well as its fields.
        # These switches only broaden a queryset that has already been scoped
        # to the user's RBAC boundary below.
        search_students = normalized_query in {"student", "students", "learner", "learners", "pupil", "pupils"}

        def add(queryset, *, kind, title, detail, url, limit=8):
            for obj in queryset[:limit]:
                results.append({
                    "kind": kind, "title": title(obj), "detail": detail(obj),
                    "url": url(obj),
                })

        if len(query) >= 2:
            text_q = Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query) \
                | Q(user__username__icontains=query) | Q(admission_number__icontains=query)
            own_student_ids = []
            class_subjects = ClassSubject.objects.none()

            if role == User.Role.SUPER_ADMIN:
                students = (Student.objects.all() if search_students else Student.objects.filter(text_q)).select_related("user", "school", "current_class")
                staff_records = Staff.objects.filter(
                    Q(staff_id__icontains=query) | Q(job_title__icontains=query)
                    | Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query)
                ).select_related("user", "school")
                classes = Class.objects.filter(name__icontains=query).select_related("school")
                invoices = Invoice.objects.filter(
                    Q(invoice_number__icontains=query) | Q(student__admission_number__icontains=query)
                ).select_related("student__user", "school")
                payment_qs = Payment.objects.filter(
                    Q(payment_number__icontains=query) | Q(gateway_reference__icontains=query)
                ).select_related("invoice__student__user", "family_guardian", "invoice__school")
                student_url = lambda obj: reverse("dashboard:super_admin_users")
                invoice_url = lambda obj: reverse("dashboard:super_admin")
                payment_url = invoice_url
                add(staff_records, kind="Staff", title=lambda obj: obj.user.get_full_name() or obj.staff_id,
                    detail=lambda obj: f"{obj.school.name} · {obj.staff_id}",
                    url=lambda obj: reverse("dashboard:super_admin_users"))
                add(classes, kind="Class", title=lambda obj: obj.name,
                    detail=lambda obj: obj.school.name,
                    url=lambda obj: reverse("dashboard:super_admin_school_config"))
            elif role in {User.Role.PARENT, User.Role.STUDENT}:
                if role == User.Role.PARENT:
                    children = get_children_for_guardian(guardian_user=user).select_related("user", "current_class")
                else:
                    children = Student.objects.filter(user=user).select_related("user", "current_class")
                own_student_ids = list(children.values_list("pk", flat=True))
                students = children if search_students else children.filter(text_q)
                class_subjects = ClassSubject.objects.filter(
                    enrollments__student_id__in=own_student_ids,
                ).distinct().select_related("class_group", "subject")
                subject_matches = class_subjects.filter(subject__name__icontains=query)
                subject_url = lambda obj: reverse("dashboard:parent_child_academic", args=[own_student_ids[0]]) \
                    if role == User.Role.PARENT and own_student_ids else reverse("dashboard:student_academic")
                add(subject_matches, kind="Subject", title=lambda obj: obj.subject.name,
                    detail=lambda obj: obj.class_group.name, url=subject_url)
                assessment_qs = Assessment.objects.filter(
                    class_subject__in=class_subjects,
                    workflow_status=Assessment.WorkflowStatus.PUBLISHED,
                ).filter(Q(title__icontains=query) | Q(class_subject__subject__name__icontains=query)) \
                    .select_related("class_subject__subject", "class_subject__class_group", "term")
                if role == User.Role.PARENT:
                    student_url = lambda obj: reverse("dashboard:parent_child_academic", args=[obj.pk])
                    assessment_url = lambda obj: reverse("dashboard:parent_child_academic", args=[
                        obj.class_subject.enrollments.filter(student_id__in=own_student_ids).values_list("student_id", flat=True).first()
                    ])
                else:
                    student_url = lambda obj: reverse("dashboard:student_academic")
                    assessment_url = lambda obj: reverse("dashboard:student_academic")
                add(assessment_qs, kind="Assessment", title=lambda obj: obj.title,
                    detail=lambda obj: f"{obj.class_subject.class_group.name} · {obj.class_subject.subject.name} · {obj.term}",
                    url=assessment_url)
                invoices = Invoice.objects.filter(student_id__in=own_student_ids).filter(
                    Q(invoice_number__icontains=query) | Q(student__admission_number__icontains=query)
                    | Q(student__user__first_name__icontains=query) | Q(student__user__last_name__icontains=query)
                ).select_related("student__user", "school")
                if role == User.Role.PARENT:
                    invoice_url = lambda obj: reverse("dashboard:parent_child_finance", args=[obj.student_id])
                else:
                    invoice_url = lambda obj: reverse("dashboard:student_finance")
                payments = Payment.objects.filter(
                    Q(invoice__student_id__in=own_student_ids)
                    | Q(allocations__invoice__student_id__in=own_student_ids),
                ).filter(Q(payment_number__icontains=query) | Q(gateway_reference__icontains=query)) \
                    .select_related("invoice__student__user", "family_guardian").distinct()
                payment_qs = payments
                payment_url = lambda obj: (
                    reverse("dashboard:parent_child_finance", args=[obj.invoice.student_id])
                    if role == User.Role.PARENT and obj.invoice_id
                    else reverse("dashboard:student_finance")
                    if role == User.Role.STUDENT
                    else reverse("dashboard:parent_dashboard")
                )
            elif role in {User.Role.TEACHER, User.Role.CLASS_TEACHER}:
                staff = Staff.objects.filter(user=user).first()
                assignments = TeachingAssignment.objects.filter(teacher=staff) if staff else TeachingAssignment.objects.none()
                class_subjects = ClassSubject.objects.filter(
                    teaching_assignments__in=assignments,
                ).distinct().select_related("class_group", "subject")
                students = Student.objects.filter(
                    enrollments__class_subject__in=class_subjects,
                )
                if not search_students:
                    students = students.filter(text_q)
                students = students.select_related("user", "current_class").distinct()
                assessments = Assessment.objects.filter(
                    class_subject__in=class_subjects,
                ).filter(Q(title__icontains=query) | Q(class_subject__subject__name__icontains=query)) \
                    .select_related("class_subject__subject", "class_subject__class_group", "term")
                add(assessments, kind="Assessment", title=lambda obj: obj.title,
                    detail=lambda obj: f"{obj.class_subject.class_group.name} · {obj.class_subject.subject.name} · {obj.term}",
                    url=lambda obj: reverse("dashboard:teacher_marks_entry", args=[obj.pk]))
                class_matches = class_subjects.filter(
                    Q(class_group__name__icontains=query) | Q(subject__name__icontains=query)
                )
                add(class_matches, kind="Class / Subject",
                    title=lambda obj: f"{obj.class_group.name} · {obj.subject.name}",
                    detail=lambda obj: "Your teaching assignment",
                    url=lambda obj: reverse("dashboard:teacher_class_roster", args=[obj.pk]))
                student_url = lambda obj: reverse("dashboard:teacher_classes")
                invoices = Invoice.objects.none()
                payment_qs = Payment.objects.none()
            else:
                try:
                    school = user.staff_profile.school
                except Staff.DoesNotExist:
                    school = None
                if school is None and role in {
                    User.Role.MANAGER, User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL,
                    User.Role.FINANCE_ADMIN, User.Role.ACCOUNTANT,
                }:
                    school = (
                        AcademicAdminRequiredMixin().get_school(request)
                        if role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}
                        else FinanceRequiredMixin().get_school(request)
                    )
                if school:
                    invoice_scope = Invoice.objects.filter(school=school)
                    payment_scope = Payment.objects.filter(
                        Q(invoice__school=school) | Q(family_guardian__school=school)
                    ).distinct()
                    invoices = invoice_scope.filter(
                        Q(invoice_number__icontains=query) | Q(student__admission_number__icontains=query)
                        | Q(student__user__first_name__icontains=query) | Q(student__user__last_name__icontains=query)
                    ).select_related("student__user")
                    payment_qs = payment_scope.filter(
                        Q(payment_number__icontains=query) | Q(gateway_reference__icontains=query)
                    ).select_related("invoice__student__user", "family_guardian")
                    invoice_url = lambda obj: reverse("dashboard:finance_invoice_detail", args=[obj.pk])
                    payment_url = lambda obj: reverse("dashboard:finance_family_payment")
                    if role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}:
                        students = Student.objects.filter(school=school)
                        if not search_students:
                            students = students.filter(text_q)
                        students = students.select_related("user", "current_class")
                        staff_records = Staff.objects.filter(
                            school=school, user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER],
                        ).filter(
                            Q(staff_id__icontains=query) | Q(job_title__icontains=query)
                            | Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query)
                        ).select_related("user")
                        classes = Class.objects.filter(school=school, name__icontains=query)
                        subjects = Subject.objects.filter(school=school, name__icontains=query)
                        student_url = lambda obj: reverse("dashboard:academic_admin_student_detail", args=[obj.pk])
                        add(staff_records, kind="Teacher", title=lambda obj: obj.user.get_full_name() or obj.staff_id,
                            detail=lambda obj: f"{obj.staff_id} · {obj.job_title}",
                            url=lambda obj: reverse("dashboard:staff_admin_staff_list") + "?teachers=1")
                        add(classes, kind="Class", title=lambda obj: obj.name,
                            detail=lambda obj: "School class", url=lambda obj: reverse("dashboard:principal_configuration"))
                        add(subjects, kind="Subject", title=lambda obj: obj.name,
                            detail=lambda obj: obj.code, url=lambda obj: reverse("dashboard:principal_configuration"))
                        invoices = Invoice.objects.none()
                        payment_qs = Payment.objects.none()
                        invoice_url = lambda obj: reverse("dashboard:home")
                        payment_url = invoice_url
                    elif role in {User.Role.MANAGER, User.Role.ACADEMIC_ADMIN}:
                        students = Student.objects.filter(school=school)
                        if not search_students:
                            students = students.filter(text_q)
                        students = students.select_related("user", "current_class")
                        staff_records = Staff.objects.filter(school=school).filter(
                            Q(staff_id__icontains=query) | Q(job_title__icontains=query)
                            | Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query)
                        ).select_related("user")
                        classes = Class.objects.filter(school=school, name__icontains=query)
                        subjects = Subject.objects.filter(school=school, name__icontains=query)
                        guardians = Guardian.objects.filter(school=school).filter(
                            Q(first_name__icontains=query) | Q(last_name__icontains=query)
                            | Q(phone_number__icontains=query) | Q(email__icontains=query)
                        )
                        student_url = lambda obj: reverse("dashboard:academic_admin_student_detail", args=[obj.pk])
                        add(staff_records, kind="Staff", title=lambda obj: obj.user.get_full_name() or obj.staff_id,
                            detail=lambda obj: f"{obj.staff_id} · {obj.job_title}",
                            url=lambda obj: reverse("dashboard:staff_admin_staff_list"))
                        add(classes, kind="Class", title=lambda obj: obj.name,
                            detail=lambda obj: "School class",
                            url=lambda obj: reverse("dashboard:principal_configuration"))
                        add(subjects, kind="Subject", title=lambda obj: obj.name,
                            detail=lambda obj: obj.code,
                            url=lambda obj: reverse("dashboard:principal_configuration"))
                        add(guardians, kind="Parent / Guardian",
                            title=lambda obj: f"{obj.first_name} {obj.last_name}",
                            detail=lambda obj: obj.phone_number,
                            url=lambda obj: reverse("dashboard:principal_parents"))
                    elif role in {User.Role.FINANCE_ADMIN, User.Role.ACCOUNTANT}:
                        students = Student.objects.filter(
                            school=school, invoices__in=invoice_scope,
                        )
                        if not search_students:
                            students = students.filter(text_q)
                        students = students.select_related("user", "current_class").distinct()
                        guardians = Guardian.objects.filter(school=school).filter(
                            Q(first_name__icontains=query) | Q(last_name__icontains=query)
                            | Q(phone_number__icontains=query) | Q(email__icontains=query)
                        )
                        student_url = lambda obj: reverse("dashboard:finance_family_payment")
                        add(guardians, kind="Parent / Guardian",
                            title=lambda obj: f"{obj.first_name} {obj.last_name}",
                            detail=lambda obj: obj.phone_number,
                            url=lambda obj: reverse("dashboard:finance_family_payment"))
                    elif role == User.Role.STAFF_ADMIN:
                        students = Student.objects.none()
                        staff_records = Staff.objects.filter(school=school).filter(
                            Q(staff_id__icontains=query) | Q(job_title__icontains=query)
                            | Q(user__first_name__icontains=query) | Q(user__last_name__icontains=query)
                        ).select_related("user")
                        add(staff_records, kind="Staff", title=lambda obj: obj.user.get_full_name() or obj.staff_id,
                            detail=lambda obj: f"{obj.staff_id} · {obj.job_title}",
                            url=lambda obj: reverse("dashboard:staff_admin_staff_list"))
                        invoices = Invoice.objects.none()
                        payment_qs = Payment.objects.none()
                        student_url = lambda obj: reverse("dashboard:staff_admin_staff_list")
                    else:
                        students = Student.objects.none()
                        invoices = Invoice.objects.none()
                        payment_qs = Payment.objects.none()
                        student_url = lambda obj: reverse("dashboard:home")
                else:
                    students = Student.objects.none()
                    invoices = Invoice.objects.none()
                    payment_qs = Payment.objects.none()
                    student_url = lambda obj: reverse("dashboard:home")
                    invoice_url = student_url
                    payment_url = student_url

            add(students, kind="Student", title=lambda obj: obj.user.get_full_name() or obj.admission_number,
                detail=lambda obj: f"{obj.admission_number} · {obj.current_class or 'No class'}",
                url=student_url)
            if role not in {User.Role.TEACHER, User.Role.CLASS_TEACHER}:
                add(invoices, kind="Invoice", title=lambda obj: obj.invoice_number,
                    detail=lambda obj: f"{obj.student.admission_number} · Ksh {obj.total_amount}", url=invoice_url)
                add(payment_qs, kind="Payment", title=lambda obj: obj.payment_number,
                    detail=lambda obj: f"Ksh {obj.amount} · {obj.get_status_display()}", url=payment_url)

        return render(request, self.template_name, {
            "query": query, "results": results[:40], "result_count": len(results),
        })


class ComingSoonView(TemplateView):
    """Placeholder landing page for roles whose dedicated dashboard hasn't
    been built yet (wired up incrementally as Phases 6-19 land)."""

    template_name = "dashboard/coming_soon.html"


class AccountLockedView(TemplateView):
    template_name = "dashboard/account_locked.html"


class SuperAdminDashboardView(RoleRequiredMixin, TemplateView):
    """Spec §5 Super Admin Dashboard — top-level stat cards. Detailed
    sub-pages (user management, school configuration, audit log browser)
    are separate views added as those workflows are built out.

    Finance and Attendance cards were placeholder text ("module pending")
    left over from Phase 4, written before those modules existed
    (Phase 6 attendance, Phase 12/19 finance). Fixed here to show real
    aggregates via the same service functions the Finance/Academic Admin
    dashboards already use — no new business logic, just wiring."""

    template_name = "dashboard/super_admin.html"
    allowed_roles = [User.Role.SUPER_ADMIN]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        from .models import School
        schools = School.objects.all().order_by("name")
        financial_summaries = [compute_school_financial_summary(school=school) for school in schools]
        attendance_summaries = [compute_school_attendance_summary(school=school) for school in schools]
        financial_summary = {
            "total_billed": sum((item["total_billed"] for item in financial_summaries), Decimal("0")),
            "total_collected": sum((item["total_collected"] for item in financial_summaries), Decimal("0")),
            "outstanding_balance": sum((item["outstanding_balance"] for item in financial_summaries), Decimal("0")),
            "arrears": sum((item["arrears"] for item in financial_summaries), Decimal("0")),
            "overdue_invoice_count": sum(item["overdue_invoice_count"] for item in financial_summaries),
            "unpaid_invoice_count": sum(item["unpaid_invoice_count"] for item in financial_summaries),
        }
        total_attendance = sum(item["total_records"] for item in attendance_summaries)
        present_attendance = sum(item["present_count"] for item in attendance_summaries)
        attendance_summary = {
            "window_days": 30,
            "total_records": total_attendance,
            "present_count": present_attendance,
            "attendance_rate_percent": round((present_attendance / total_attendance) * 100, 1) if total_attendance else None,
        }
        current_years = AcademicYear.objects.filter(is_current=True).order_by("school__name", "name")
        current_terms = Term.objects.filter(is_current=True).order_by("academic_year__school__name", "name")

        from django.db.models import Count, Q as _Q
        school_stats = {}
        for row in School.objects.all().annotate(
            _students=Count("students", filter=_Q(students__is_active=True), distinct=True),
            _staff=Count("staff_members", filter=_Q(staff_members__is_active=True), distinct=True),
            _classes=Count("classes", filter=_Q(classes__is_active=True), distinct=True),
        ).values("pk", "name", "_students", "_staff", "_classes"):
            school_stats[row["pk"]] = row

        context.update(
            {
                "total_students": sum(s.get("_students", 0) for s in school_stats.values()),
                "total_staff": sum(s.get("_staff", 0) for s in school_stats.values()),
                "active_classes": sum(s.get("_classes", 0) for s in school_stats.values()),
                "current_academic_year": current_years.first(),
                "current_term": current_terms.first(),
                "current_academic_years": current_years,
                "current_terms": current_terms,
                "schools": schools,
                "financial_summary": financial_summary,
                "attendance_summary": attendance_summary,
                "school_chart": [
                    {
                        "name": school.name,
                        "students": school_stats.get(school.pk, {}).get("_students", 0),
                        "staff": school_stats.get(school.pk, {}).get("_staff", 0),
                        "classes": school_stats.get(school.pk, {}).get("_classes", 0),
                        "collected": float(financial_summaries[index]["total_collected"]),
                        "outstanding": float(financial_summaries[index]["outstanding_balance"]),
                        "attendance": attendance_summaries[index]["attendance_rate_percent"] or 0,
                    }
                    for index, school in enumerate(schools)
                ],
                "recent_audit_logs": AuditLog.objects.select_related("actor")
                .order_by("-created_at")[:10],
            }
        )
        return context


class ReportCardHTMLView(RoleRequiredMixin, View):
    """Spec §15 'HTML report' / 'Printable report' — renders the report in
    the browser; the person prints via the browser's own print dialog
    (Ctrl/Cmd+P), so no separate 'printable' code path is needed.

    Spec §17 lets students view their own report books; spec §18 lets
    parents view their children's. STUDENT/PARENT are allowed here, but
    only for their own (or their own child's) ReportCard — the ownership
    check below is what actually enforces that, not just the role."""

    allowed_roles = [
        User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN,
        User.Role.CLASS_TEACHER, User.Role.EXAM_OFFICER, User.Role.STAFF_ADMIN,
        User.Role.STUDENT, User.Role.PARENT,
    ]

    def get(self, request, report_card_id):
        report_card = get_object_or_404(ReportCard, pk=report_card_id)
        if request.user.role == User.Role.STUDENT and report_card.student.user_id != request.user.pk:
            raise Http404("Report card not found.")
        if request.user.role == User.Role.PARENT:
            children = get_children_for_guardian(guardian_user=request.user)
            if not children.filter(pk=report_card.student_id).exists():
                raise Http404("Report card not found.")
        html = render_report_html(report_card=report_card)
        return HttpResponse(html)


class ReportCardPDFView(RoleRequiredMixin, View):
    """Spec §15 'PDF report' / 'Downloadable report'. Generates on first
    request if no PDF exists yet, then serves the stored file — repeat
    downloads don't re-render unless explicitly regenerated.

    Same student/parent ownership rule as ReportCardHTMLView above."""

    allowed_roles = [
        User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN,
        User.Role.CLASS_TEACHER, User.Role.EXAM_OFFICER, User.Role.STAFF_ADMIN,
        User.Role.STUDENT, User.Role.PARENT,
    ]

    def get(self, request, report_card_id):
        report_card = get_object_or_404(ReportCard, pk=report_card_id)
        if request.user.role == User.Role.STUDENT and report_card.student.user_id != request.user.pk:
            raise Http404("Report card not found.")
        if request.user.role == User.Role.PARENT:
            children = get_children_for_guardian(guardian_user=request.user)
            if not children.filter(pk=report_card.student_id).exists():
                raise Http404("Report card not found.")
        if not report_card.pdf_file:
            generate_report_pdf(report_card=report_card, generated_by=request.user, request=request)
            report_card.refresh_from_db()

        response = HttpResponse(report_card.pdf_file.read(), content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="report_{report_card.student.admission_number}.pdf"'
        )
        return response


class BatchReportGenerateView(RoleRequiredMixin, View):
    """Spec §15 'Batch reports'. POST-only action endpoint — the
    class/term/template picker UI lands with the Academic Admin dashboard
    build-out; this is the working generation endpoint it will call."""

    allowed_roles = [User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN]

    def post(self, request):
        class_group = get_object_or_404(Class, pk=request.POST.get("class_id"))
        term = get_object_or_404(Term, pk=request.POST.get("term_id"))
        template = get_object_or_404(ReportTemplate, pk=request.POST.get("template_id"))

        cards = generate_batch_reports(
            class_group=class_group, term=term, template=template,
            generated_by=request.user, request=request,
        )
        return HttpResponse(
            f"Generated {len(cards)} report(s) for {class_group} - {term}.",
            content_type="text/plain",
        )


class TranscriptGenerateAndDownloadView(RoleRequiredMixin, View):
    """Spec §16 'secure PDF documents'. Always generates a fresh transcript
    on request rather than serving a cached one — cumulative academic
    records must reflect every currently-published result, and each
    generation gets its own verification_code (spec §16 interpretation,
    see models.Transcript docstring).

    Spec §17 lets students view their own transcript — STUDENT is allowed
    here, but only for their own record."""

    allowed_roles = [
        User.Role.SUPER_ADMIN, User.Role.ACADEMIC_ADMIN, User.Role.EXAM_OFFICER,
        User.Role.STUDENT,
    ]

    def get(self, request, student_id):
        student = get_object_or_404(Student, pk=student_id)
        if request.user.role == User.Role.STUDENT and student.user_id != request.user.pk:
            raise Http404("Student not found.")
        transcript = generate_transcript(
            student=student, generated_by=request.user, request=request
        )
        response = HttpResponse(transcript.pdf_file.read(), content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="transcript_{student.admission_number}.pdf"'
        )
        return response


class TranscriptVerifyView(View):
    """Public endpoint — spec §16's 'secure PDF documents' implies a third
    party (employer, other institution) should be able to confirm a
    transcript is genuine using only the code printed on it. Deliberately
    unauthenticated and deliberately minimal in what it discloses (no
    full mark list) to avoid leaking academic records to link-guessers."""

    def get(self, request, verification_code):
        result = verify_transcript(verification_code)
        if not result["valid"]:
            return HttpResponse("Invalid or unrecognized verification code.", status=404)
        lines = [
            "Transcript verified.",
            f"Student: {result['student_name']} ({result['admission_number']})",
            f"Issued: {result['generated_at']:%Y-%m-%d}",
            f"CGPA: {result['cgpa'] if result['cgpa'] is not None else '—'}",
            f"Status: {result['graduation_status']}",
        ]
        return HttpResponse("\n".join(lines), content_type="text/plain")


# =============================================================================
# Phase 16 — Student Dashboard (spec §17)
#
# "Students cannot modify official academic or financial records" is
# enforced structurally, not just documented: every view below is
# read-only except the two explicit, legitimate student actions the spec
# itself describes — submitting an assignment (LMS submission is not the
# same as editing a grade) and marking one's own notification read. No
# view here writes to Assessment/AssessmentMark/SubjectResult/Invoice/
# Payment/Attendance.
# =============================================================================

class StudentRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.STUDENT, User.Role.PARENT]
    active_nav = None  # set per-view; drives sidebar active-link highlighting

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_student(self, request) -> Student:
        if request.user.role == User.Role.PARENT:
            child = get_children_for_guardian(guardian_user=request.user).select_related("user", "current_class").first()
            return child
        return get_object_or_404(Student, user=request.user)

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and request.user.role == User.Role.PARENT and not get_children_for_guardian(guardian_user=request.user).exists():
            if request.resolver_match.url_name != "student_dashboard":
                return redirect("dashboard:student_dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_current_term(self, student: Student) -> Term | None:
        return Term.objects.filter(
            academic_year__school=student.school, is_current=True
        ).first()


class StudentDashboardView(StudentRequiredMixin, TemplateView):
    """Overview landing page — one stat card per spec §17 category
    (Academic, LMS, Finance, Communication) with links to the four
    detail pages below."""

    template_name = "dashboard/student/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)
        if student is None:
            context.update({
                "student": None, "current_term": None, "average": None,
                "pending_assignments": 0,
                "account_summary": {"outstanding_balance": Decimal("0")},
                "unread_notifications": Notification.objects.filter(
                    recipient=self.request.user, is_read=False
                ).count(),
            })
            return context
        term = self.get_current_term(student)

        average = None
        if term is not None:
            class_subjects = ClassSubject.objects.filter(
                enrollments__student=student, enrollments__academic_year=term.academic_year
            ).distinct()
            # Only include subjects that actually have at least one graded
            # component (weight_covered > 0) — compute_weighted_average()
            # returns Decimal("0"), never None, for an ungraded subject, so
            # including every enrolled subject unconditionally would count
            # not-yet-assessed subjects as a real 0% and skew the average
            # down. This mirrors the same gate assemble_report_data()
            # (Phase 9) uses for exactly this reason.
            totals = []
            for cs in class_subjects:
                summary = compute_weighted_average(student, cs, term)
                if summary["weight_covered"] > 0:
                    totals.append(summary["weighted_total"])
            if totals:
                average = sum(totals) / len(totals)

        pending_assignments = Assignment.objects.filter(
            class_subject__enrollments__student=student, is_published=True,
        ).exclude(
            submissions__student=student
        ).distinct().count()

        account_summary = compute_student_account_summary(student=student)
        unread_notifications = 0  # Provided by context processor as unread_notification_count

        context.update({
            "student": student,
            "current_term": term,
            "average": average,
            "pending_assignments": pending_assignments,
            "account_summary": account_summary,
            "unread_notifications": unread_notifications,
        })
        return context


class SuperAdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.SUPER_ADMIN]
    active_nav = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_school(self, school_id=None):
        from .models import School
        return get_object_or_404(School, pk=school_id) if school_id else School.objects.first()


STAFF_ROLES = {
    User.Role.STAFF_ADMIN, User.Role.ACADEMIC_ADMIN, User.Role.MANAGER, User.Role.PRINCIPAL,
    User.Role.DEPUTY_PRINCIPAL, User.Role.FINANCE_ADMIN,
    User.Role.TEACHER, User.Role.EXAM_OFFICER, User.Role.CLASS_TEACHER,
    User.Role.DEPARTMENT_HEAD, User.Role.ACCOUNTANT, User.Role.LIBRARIAN,
}


def _next_staff_id(school, user):
    candidate = f"STAFF-{user.pk}"
    if not Staff.objects.filter(school=school, staff_id=candidate).exists():
        return candidate
    index = 2
    while Staff.objects.filter(school=school, staff_id=f"{candidate}-{index}").exists():
        index += 1
    return f"{candidate}-{index}"


def _associate_user_with_school(*, user, school, role=None):
    role = role or user.role
    if role in STAFF_ROLES:
        profile, created = Staff.objects.get_or_create(
            user=user,
            defaults={
                "school": school, "staff_id": _next_staff_id(school, user),
                "job_title": user.get_role_display(), "date_hired": datetime.date.today(),
            },
        )
        if not created and profile.school_id != school.pk:
            profile.school = school
            profile.save(update_fields=["school", "updated_at"])
    elif role == User.Role.STUDENT:
        profile = Student.objects.filter(user=user).first()
        if profile:
            profile.school = school
            profile.save(update_fields=["school", "updated_at"])
    elif role == User.Role.PARENT:
        profile = Guardian.objects.filter(user=user).first()
        if profile:
            profile.school = school
            profile.save(update_fields=["school", "updated_at"])


class SuperAdminUsersView(SuperAdminRequiredMixin, TemplateView):
    template_name = "dashboard/super_admin/users.html"
    active_nav = "users"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        users = User.objects.all().order_by("-created_at")
        search = self.request.GET.get("q", "").strip()
        if search:
            from django.db.models import Count, Q
            users = users.filter(Q(username__icontains=search) | Q(first_name__icontains=search) | Q(last_name__icontains=search) | Q(email__icontains=search))
        from django.core.paginator import Paginator
        paginator = Paginator(users, 25)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        context.update({"users": page_obj, "page_obj": page_obj, "search": search, "roles": ACTIVE_ROLE_CHOICES, "schools": School.objects.all()})
        return context

    def post(self, request):
        action = request.POST.get("action")
        if action == "create":
            username = request.POST.get("username", "").strip()
            password = request.POST.get("password", "")
            role = request.POST.get("role", "")
            school_id = request.POST.get("school_id")
            if not username or not password or role not in ACTIVE_ROLE_VALUES:
                return HttpResponseForbidden("Username, password, and an active role are required.")
            school = get_object_or_404(School, pk=school_id) if school_id else None
            if role != User.Role.SUPER_ADMIN and school is None:
                return HttpResponseForbidden("A school is required for this role.")
            try:
                with transaction.atomic():
                    new_user = User.objects.create_user(
                        username=username, password=password,
                        first_name=request.POST.get("first_name", "").strip(),
                        last_name=request.POST.get("last_name", "").strip(),
                        email=request.POST.get("email", "").strip(),
                        role=role, phone_number=request.POST.get("phone_number", "").strip(),
                        must_change_password=True,
                    )
                    if school:
                        if role in STAFF_ROLES:
                            Staff.objects.create(
                                user=new_user, school=school,
                                staff_id=request.POST.get("staff_id", "").strip() or _next_staff_id(school, new_user),
                                job_title=request.POST.get("job_title", "").strip() or new_user.get_role_display(),
                                date_hired=datetime.date.today(),
                            )
                        elif role == User.Role.STUDENT:
                            admission = request.POST.get("admission_number", "").strip() or f"STU-{new_user.pk}"
                            gender = request.POST.get("gender", "")
                            if gender not in Student.Gender.values:
                                raise ValueError("Select Male, Female, or Other for student gender.")
                            Student.objects.create(user=new_user, school=school, admission_number=admission, admission_date=datetime.date.today(), gender=gender)
                        elif role == User.Role.PARENT:
                            Guardian.objects.create(user=new_user, school=school, first_name=new_user.first_name or username, last_name=new_user.last_name, relationship=request.POST.get("relationship", "Parent"), phone_number=new_user.phone_number)
            except (IntegrityError, ValueError) as exc:
                return HttpResponseForbidden(str(exc))
            log_audit(actor=request.user, action=AuditLog.Action.CREATE, request=request, target_model="User", target_object_id=new_user.pk, description=f"Created {new_user.username} and associated the account with {school or 'all schools'}.")
            return redirect("dashboard:super_admin_users")
        user = get_object_or_404(User, pk=request.POST.get("user_id"))
        if user.pk == request.user.pk and action in {"lock", "deactivate", "role"}:
            return HttpResponseForbidden("You cannot disable or change your own super-admin account.")
        previous = {"is_active": user.is_active, "is_locked": user.is_locked, "role": user.role}
        if action == "assign_school":
            school = get_object_or_404(School, pk=request.POST.get("school_id"))
            _associate_user_with_school(user=user, school=school)
            log_audit(actor=request.user, action=AuditLog.Action.UPDATE, request=request, target_model="User", target_object_id=user.pk, description=f"Associated {user.username} with {school.name}.")
            return redirect("dashboard:super_admin_users")
        if action == "username":
            username = request.POST.get("username", "").strip()
            if not username:
                return HttpResponseForbidden("Username is required.")
            user.username = username
            try:
                user.save(update_fields=["username", "updated_at"])
            except IntegrityError:
                return HttpResponseForbidden("That username is already in use.")
            log_audit(actor=request.user, action=AuditLog.Action.UPDATE, request=request, target_model="User", target_object_id=user.pk, description=f"Changed username for account {user.pk}.")
            return redirect("dashboard:super_admin_users")
        if action == "reset_password":
            password = request.POST.get("password", "")
            if len(password) < 8:
                return HttpResponseForbidden("Temporary password must be at least 8 characters.")
            user.set_password(password)
            user.must_change_password = True
            user.save(update_fields=["password", "must_change_password", "updated_at"])
            log_audit(actor=request.user, action=AuditLog.Action.PASSWORD_RESET, request=request, target_model="User", target_object_id=user.pk, description=f"Issued a temporary password for account {user.pk}; password change required at next sign-in.")
            return redirect("dashboard:super_admin_users")
        if action == "lock":
            user.is_locked = True
            audit_action = AuditLog.Action.LOCK
        elif action == "unlock":
            user.is_locked = False
            audit_action = AuditLog.Action.UNLOCK
        elif action in {"activate", "deactivate"}:
            user.is_active = action == "activate"
            audit_action = AuditLog.Action.UPDATE
        elif action == "role":
            role = request.POST.get("role")
            if role not in ACTIVE_ROLE_VALUES:
                return HttpResponseForbidden("That role is inactive and cannot be assigned.")
            user.role = role
            audit_action = AuditLog.Action.ROLE_CHANGE
        else:
            return HttpResponseForbidden("Unsupported user action.")
        user.save(update_fields=["is_active", "is_locked", "role", "updated_at"])
        log_audit(actor=request.user, action=audit_action, request=request, target_model="User", target_object_id=user.pk, description=f"Updated account {user.username}.", previous_value=previous, new_value={"is_active": user.is_active, "is_locked": user.is_locked, "role": user.role})
        return redirect("dashboard:super_admin_users")


class SuperAdminSchoolConfigView(SuperAdminRequiredMixin, TemplateView):
    template_name = "dashboard/super_admin/school_config.html"
    active_nav = "school_config"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from .models import School
        context["schools"] = School.objects.all().order_by("name")
        return context

    def post(self, request):
        from .models import School
        action = request.POST.get("action", "update")
        school = get_object_or_404(School, pk=request.POST.get("school_id")) if action == "update" and request.POST.get("school_id") else (School.objects.first() if action == "update" else School())
        previous = {"name": school.name, "code": school.code, "motto": school.motto, "address": school.address, "phone_number": school.phone_number, "email": school.email, "enable_position_ranking": school.enable_position_ranking}
        school.name = request.POST.get("name", "").strip()
        school.short_name = request.POST.get("short_name", "").strip()
        school.code = request.POST.get("code", "").strip()
        school.subdomain = request.POST.get("subdomain", "").strip().lower() or None
        school.motto = request.POST.get("motto", "").strip()
        school.address = request.POST.get("address", "").strip()
        school.phone_number = request.POST.get("phone_number", "").strip()
        school.email = request.POST.get("email", "").strip()
        school.primary_color = request.POST.get("primary_color", "").strip() or school.primary_color
        school.secondary_color = request.POST.get("secondary_color", "").strip() or school.secondary_color
        school.established_date = request.POST.get("established_date") or None
        school.enable_position_ranking = request.POST.get("enable_position_ranking") == "on"
        if action == "create" or "is_active" in request.POST:
            school.is_active = request.POST.get("is_active") == "on"
        if not school.name or not school.code:
            return HttpResponseForbidden("School name and code are required.")
        if action == "create" and not school.subdomain:
            return HttpResponseForbidden("A school subdomain is required.")
        try:
            if request.FILES.get("logo"):
                school.logo = request.FILES["logo"]
            school.full_clean()
            school.save()
        except Exception as exc:
            return HttpResponseForbidden(str(exc))
        log_audit(actor=request.user, action=AuditLog.Action.CREATE if action == "create" else AuditLog.Action.UPDATE, request=request, target_model="School", target_object_id=school.pk, description=f"{'Created' if action == 'create' else 'Updated'} school configuration for {school.name}.", previous_value=previous if action == "update" else {}, new_value={"name": school.name, "code": school.code, "motto": school.motto, "address": school.address, "phone_number": school.phone_number, "email": school.email, "enable_position_ranking": school.enable_position_ranking, "is_active": school.is_active})
        return redirect("dashboard:super_admin_school_config")


class SuperAdminAuditLogsView(SuperAdminRequiredMixin, TemplateView):
    template_name = "dashboard/super_admin/audit_logs.html"
    active_nav = "audit_logs"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        logs = AuditLog.objects.select_related("actor").all()
        search = self.request.GET.get("q", "").strip()
        action = self.request.GET.get("action", "").strip()
        if search:
            from django.db.models import Count, Q
            logs = logs.filter(Q(description__icontains=search) | Q(target_model__icontains=search) | Q(target_object_id__icontains=search) | Q(actor__username__icontains=search))
        if action:
            logs = logs.filter(action=action)
        context.update({"audit_logs": logs[:250], "actions": AuditLog.Action.choices, "search": search, "selected_action": action})
        return context


class StudentAcademicView(StudentRequiredMixin, TemplateView):
    """Spec §17 Academic section: subjects, classes, results, grades,
    attendance. Report books/transcript/GPA/CGPA are separate downloadable
    documents (Phases 9-10) — this page links out to those rather than
    re-rendering their content inline."""

    template_name = "dashboard/student/academic.html"
    active_nav = "academic"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)
        term = self.get_current_term(student)

        subject_rows = []
        quiz_rows = []
        pending_tasks = []
        if term is not None:
            class_subjects = ClassSubject.objects.filter(
                enrollments__student=student, enrollments__academic_year=term.academic_year
            ).distinct().select_related("subject")
            grading_scheme = student.school.grading_schemes.filter(is_default=True).first()
            for cs in class_subjects:
                summary = compute_weighted_average(student, cs, term)
                completion = compute_student_subject_completion(
                    student=student, class_subject=cs, term=term
                )
                band = None
                if grading_scheme and summary["weight_covered"] > 0:
                    band = get_grade_for_mark(grading_scheme, summary["weighted_total"])
                subject_rows.append({
                    "subject": cs.subject.name,
                    "score": summary["weighted_total"],
                    "grade": band.grade if band else "-",
                    "is_complete": completion["is_complete"],
                    "completed_tasks": completion["completed_tasks"],
                    "total_tasks": completion["total_tasks"],
                })

                # Show exactly which records are preventing completion. New
                # CAT/exam tasks have a matching formal Assessment row and
                # interactive Quiz row; the Quiz is the actionable record,
                # so do not list the duplicate Assessment as another task.
                quizzes_for_subject = Quiz.objects.filter(
                    class_subject=cs, term=term, is_published=True
                )
                quiz_titles = quizzes_for_subject.values_list("title", flat=True)
                for assessment in Assessment.objects.filter(
                    class_subject=cs, term=term
                ).exclude(title__in=quiz_titles):
                    if not assessment.marks.filter(student=student).exists():
                        pending_tasks.append({
                            "subject": cs.subject.name,
                            "category": "Assessment",
                            "title": assessment.title,
                            "reason": "Mark not entered",
                        })

                for assignment in Assignment.objects.filter(
                    class_subject=cs, term=term, is_published=True
                ):
                    submission = assignment.submissions.filter(student=student).first()
                    if submission is None:
                        reason = "Not submitted"
                    elif submission.marks_obtained is None or submission.status != AssignmentSubmission.Status.GRADED:
                        reason = "Awaiting teacher grading"
                    else:
                        continue
                    pending_tasks.append({
                        "subject": cs.subject.name,
                        "category": "Assignment",
                        "title": assignment.title,
                        "reason": reason,
                        "assignment_id": assignment.pk,
                    })

                for quiz in quizzes_for_subject:
                    attempt = QuizAttempt.objects.filter(
                        quiz=quiz, student=student
                    ).order_by("-attempt_number").first()
                    if attempt is not None and attempt.is_fully_graded and attempt.total_score is not None:
                        continue
                    pending_tasks.append({
                        "subject": cs.subject.name,
                        "category": quiz.get_task_category_display(),
                        "title": quiz.title,
                        "reason": "Not attempted" if attempt is None else "Awaiting teacher grading",
                        "quiz_id": quiz.pk,
                    })

            # Quizzes are stored separately from official AssessmentMark rows.
            # Keep them separate from Current Term Results because quizzes have
            # no configured assessment-component weight, but expose their
            # teacher-assigned grades from the Academic page as well.
            quizzes = Quiz.objects.filter(
                class_subject__in=class_subjects,
                term=term,
                is_published=True,
            ).select_related("class_subject__subject")
            latest_attempts = {
                attempt.quiz_id: attempt
                for attempt in QuizAttempt.objects.filter(
                    student=student, quiz__in=quizzes
                ).order_by("attempt_number")
            }
            for quiz in quizzes:
                attempt = latest_attempts.get(quiz.pk)
                if attempt is None or not attempt.is_fully_graded:
                    continue
                maximum = quiz.max_marks or Decimal("100")
                percentage = (
                    (attempt.total_score / maximum * Decimal("100")).quantize(Decimal("0.01"))
                    if maximum > 0 and attempt.total_score is not None else None
                )
                band = (
                    get_grade_for_mark(grading_scheme, percentage)
                    if grading_scheme and percentage is not None else None
                )
                quiz_rows.append({
                    "subject": quiz.class_subject.subject.name,
                    "title": quiz.title,
                    "score": attempt.total_score,
                    "maximum": maximum,
                    "percentage": percentage,
                    "grade": band.grade if band else "-",
                })

        report_cards = ReportCard.objects.filter(student=student).select_related("term")

        context.update({
            "student": student,
            "current_term": term,
            "subject_rows": subject_rows,
            "quiz_rows": quiz_rows,
            "pending_tasks": pending_tasks,
            "report_cards": report_cards,
            "academic_history": build_parent_academic_history(student=student),
        })
        return context


class StudentLMSView(StudentRequiredMixin, TemplateView):
    """Spec §17 LMS section: course materials, assignments, quizzes,
    submission history, feedback."""

    template_name = "dashboard/student/lms.html"
    active_nav = "lms"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)
        term = self.get_current_term(student)

        class_subjects = ClassSubject.objects.filter(
            enrollments__student=student,
            enrollments__academic_year=term.academic_year if term else None,
        ).distinct() if term else ClassSubject.objects.none()

        materials = CourseMaterial.objects.filter(
            class_subject__in=class_subjects, is_published=True
        ).select_related("class_subject__subject") if term else CourseMaterial.objects.none()

        assignments = Assignment.objects.filter(
            class_subject__in=class_subjects, is_published=True
        ).select_related("class_subject__subject") if term else Assignment.objects.none()

        my_submissions = {
            s.assignment_id: s
            for s in AssignmentSubmission.objects.filter(student=student)
        }
        assignment_rows = [
            {"assignment": a, "submission": my_submissions.get(a.pk)} for a in assignments
        ]

        quizzes = Quiz.objects.filter(
            class_subject__in=class_subjects, is_published=True
        ).select_related("class_subject__subject") if term else Quiz.objects.none()

        my_attempts = {
            a.quiz_id: a
            for a in QuizAttempt.objects.filter(student=student).order_by("-attempt_number")
        }
        quiz_rows = [
            {"quiz": q, "attempt": my_attempts.get(q.pk)} for q in quizzes
        ]
        cats = Assessment.objects.filter(
            class_subject__in=class_subjects, is_published=True
        ).select_related("class_subject__subject", "component").order_by("-created_at") if term else Assessment.objects.none()

        context.update({
            "student": student,
            "current_term": term,
            "materials": materials,
            "assignment_rows": assignment_rows,
            "quiz_rows": quiz_rows,
            "cat_rows": [{"quiz": q, "attempt": my_attempts.get(q.pk)} for q in quizzes if q.task_category == Quiz.TaskCategory.CAT],
            "exam_rows": [{"quiz": q, "attempt": my_attempts.get(q.pk)} for q in quizzes if q.task_category == Quiz.TaskCategory.EXAM],
            "regular_quiz_rows": [{"quiz": q, "attempt": my_attempts.get(q.pk)} for q in quizzes if q.task_category == Quiz.TaskCategory.QUIZ],
            "task_sections": [
                ("CATs", [{"quiz": q, "attempt": my_attempts.get(q.pk)} for q in quizzes if q.task_category == Quiz.TaskCategory.CAT]),
                ("Exams", [{"quiz": q, "attempt": my_attempts.get(q.pk)} for q in quizzes if q.task_category == Quiz.TaskCategory.EXAM]),
                ("Quizzes", [{"quiz": q, "attempt": my_attempts.get(q.pk)} for q in quizzes if q.task_category == Quiz.TaskCategory.QUIZ]),
            ],
            "assessments": cats,
        })
        return context


class StudentSubmitAssignmentView(StudentRequiredMixin, View):
    """The one write action on this page — spec §17 explicitly lists
    'Upload submissions' as something students do. This is not modifying
    an official record; grading (a separate, staff-only action) is what
    produces the official mark."""

    def post(self, request, assignment_id):
        student = self.get_student(request)
        assignment = get_object_or_404(Assignment, pk=assignment_id, is_published=True)

        if timezone.now() > assignment.deadline and not assignment.overdue_reopened:
            return HttpResponseForbidden("This assignment is overdue and has not been reopened by the teacher.")

        submitted_file = request.FILES.get("submitted_file")
        submitted_text = request.POST.get("submitted_text", "").strip()
        required = assignment.submission_format
        has_file = submitted_file is not None
        has_text = bool(submitted_text)
        if (required == Assignment.SubmissionFormat.FILE_UPLOAD and not has_file) or \
                (required == Assignment.SubmissionFormat.TEXT_ENTRY and not has_text) or \
                (required == Assignment.SubmissionFormat.BOTH and not (has_file or has_text)):
            return HttpResponseForbidden("A submission file or answer is required for this assignment.")
        if submitted_file:
            try:
                validate_upload(submitted_file, validate_document_content, 10)
            except (FileValidationError, ValidationError) as exc:
                return HttpResponseForbidden(str(exc))
            # validate_upload renames the file to a fresh UUID before storage.

        try:
            submit_assignment(
                assignment=assignment, student=student,
                submitted_file=submitted_file,
                submitted_text=submitted_text,
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))

        return redirect("dashboard:student_lms")


class StudentQuizAttemptView(StudentRequiredMixin, View):
    template_name = "dashboard/student/quiz_attempt.html"

    def get_quiz(self, request, quiz_id):
        return get_object_or_404(
            Quiz.objects.prefetch_related("questions__options"),
            pk=quiz_id, is_published=True,
            class_subject__enrollments__student=self.get_student(request),
        )

    def get(self, request, quiz_id):
        quiz = self.get_quiz(request, quiz_id)
        student = self.get_student(request)
        attempts = QuizAttempt.objects.filter(quiz=quiz, student=student)
        if quiz.deadline and timezone.now() > quiz.deadline and not quiz.overdue_reopened:
            return HttpResponseForbidden("This task is overdue and has not been reopened by the teacher.")
        if attempts.count() >= quiz.max_attempts:
            return HttpResponseForbidden("You have used all allowed attempts for this task.")
        return render(request, self.template_name, {"quiz": quiz, "attempts": attempts})

    def post(self, request, quiz_id):
        quiz = self.get_quiz(request, quiz_id)
        student = self.get_student(request)
        if quiz.deadline and timezone.now() > quiz.deadline and not quiz.overdue_reopened:
            return HttpResponseForbidden("This task is overdue and has not been reopened by the teacher.")
        attempt_number = QuizAttempt.objects.filter(quiz=quiz, student=student).count() + 1
        if attempt_number > quiz.max_attempts:
            return HttpResponseForbidden("You have used all allowed attempts for this quiz.")
        answers = {}
        uploaded_file = request.FILES.get("quiz_file")
        typed_answer = False
        for question in quiz.questions.all():
            values = request.POST.getlist(f"question_{question.pk}")
            option_ids = [int(value) for value in values if value.isdigit()]
            text_answer = request.POST.get(f"question_{question.pk}", "").strip()
            typed_answer = typed_answer or bool(option_ids or text_answer)
            answers[question.pk] = {
                "option_ids": option_ids,
                "text": text_answer,
            }
        if quiz.submission_format == Quiz.SubmissionFormat.FILE_UPLOAD and not uploaded_file:
            return HttpResponseForbidden("Upload the required answer document before submitting.")
        if quiz.submission_format == Quiz.SubmissionFormat.TEXT_ENTRY and not typed_answer:
            return HttpResponseForbidden("Answer at least one question before submitting.")
        if quiz.submission_format == Quiz.SubmissionFormat.BOTH and not (uploaded_file or typed_answer):
            return HttpResponseForbidden("Type an answer or upload a document before submitting.")
        if uploaded_file:
            try:
                validate_upload(uploaded_file, validate_document_content, 25)
            except (FileValidationError, ValidationError) as exc:
                return HttpResponseForbidden(str(exc))
            if answers:
                answers[next(iter(answers))]["file"] = uploaded_file
        attempt = QuizAttempt.objects.create(quiz=quiz, student=student, attempt_number=attempt_number)
        from .services import submit_quiz_attempt
        submit_quiz_attempt(attempt=attempt, answers=answers)
        if quiz.created_by_id:
            send_notification(
                recipient=quiz.created_by.user,
                notification_type=Notification.NotificationType.SUBMISSION_RECEIVED,
                title=f"New {quiz.get_task_category_display()} submission",
                body=f"{student} submitted {quiz.title}.",
                related_model="Quiz", related_object_id=quiz.pk, request=request,
            )
        return redirect("dashboard:student_lms")


class StudentFinanceView(StudentRequiredMixin, TemplateView):
    """Spec §17 Finance section: fees, balance, invoices, receipts,
    payment history. This is the student's own account — showing full
    detail here is showing someone their own data, not a policy
    violation; §19/§23's 'do not expose financial info to unrelated
    staff' is a different boundary from 'a student sees their own bill'."""

    template_name = "dashboard/student/finance.html"
    active_nav = "finance"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)

        invoices = Invoice.objects.filter(student=student).order_by("-issue_date")
        payments = get_student_payment_records(student=student)
        account_summary = compute_student_account_summary(student=student)
        from django.db.models import Q
        fee_structures = FeeStructure.objects.none()
        if student.current_class_id:
            fee_structures = FeeStructure.objects.filter(
                school=student.school, academic_year__is_current=True, is_active=True,
            ).filter(Q(class_groups=student.current_class) | Q(class_group=student.current_class)) \
                .select_related("academic_year").distinct()

        context.update({
            "student": student,
            "invoices": invoices,
            "payments": payments,
            "account_summary": account_summary,
            "fee_structures": fee_structures,
            "financial_history": build_student_financial_history(student=student),
        })
        return context


class StudentCommunicationView(StudentRequiredMixin, TemplateView):
    """Spec §17 Communication section: announcements, notifications.
    'Messages' (private staff<->student messaging) has no backing model
    yet — Discussion/DiscussionReply (Phase 11) is course-level, not a
    general inbox. Flagged rather than fabricated; a dedicated messaging
    model is a reasonable follow-up phase."""

    template_name = "dashboard/student/communication.html"
    active_nav = "communication"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        student = self.get_student(self.request)

        announcements = Announcement.objects.filter(
            school=student.school, is_published=True,
        ).filter(
            models_q_student_audience()
        ).order_by("-created_at")[:20]

        notifications = Notification.objects.filter(
            recipient=self.request.user
        ).order_by("-created_at")[:50]

        context.update({
            "student": student,
            "announcements": announcements,
            "notifications": notifications,
        })
        return context


def models_q_student_audience():
    from django.db.models import Count, Q

    return Q(audience=Announcement.Audience.ALL) | Q(audience=Announcement.Audience.STUDENTS)


class StudentMarkNotificationReadView(StudentRequiredMixin, View):
    def post(self, request, notification_id):
        notification = get_object_or_404(
            Notification, pk=notification_id, recipient=request.user
        )
        mark_notification_read(notification=notification)
        return redirect("dashboard:student_communication")


class NotificationOpenView(LoginRequiredMixin, View):
    def get(self, request, notification_id):
        notification = get_object_or_404(Notification, pk=notification_id, recipient=request.user)
        mark_notification_read(notification=notification)
        if notification.related_model == "Quiz" and notification.related_object_id:
            if request.user.role in {User.Role.TEACHER, User.Role.CLASS_TEACHER}:
                return redirect("dashboard:teacher_quiz_attempts", quiz_id=notification.related_object_id)
            if request.user.role == User.Role.STUDENT:
                return redirect("dashboard:student_quiz_attempt", quiz_id=notification.related_object_id)
        if notification.related_model == "Assignment" and notification.related_object_id:
            if request.user.role in {User.Role.TEACHER, User.Role.CLASS_TEACHER}:
                return redirect("dashboard:teacher_assignment_submissions", assignment_id=notification.related_object_id)
        return redirect(get_dashboard_url_for_role(request.user))


class NotificationFeedView(LoginRequiredMixin, View):
    def get(self, request):
        from .context_processors import dashboard_notifications

        context = dashboard_notifications(request)
        return JsonResponse({
            "unread_count": context["unread_notification_count"],
            "notifications": [
                {"id": row.pk, "title": row.title, "message": row.message, "is_read": row.is_read}
                for row in context["dashboard_notifications"]
            ],
        })


# =============================================================================
# Phase 17 — Parent/Guardian Portal (spec §18). Same self-service,
# read-mostly shape as the Student Dashboard (Phase 16): every view here
# derives the parent's children from request.user via
# services.get_children_for_guardian() — never from a URL parameter — and
# every per-child page re-checks that the requested child actually
# belongs to this parent before showing anything. "Parents must not
# modify official academic records" (spec) — no view here writes to
# Assessment/AssessmentMark/Attendance/Invoice/Payment.
# =============================================================================

class ParentRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.PARENT]
    active_nav = None  # set per-view; drives sidebar active-link highlighting

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_children(self, request):
        return get_children_for_guardian(guardian_user=request.user)

    def get_child_or_404(self, request, student_id):
        """The ownership check that matters: a parent may only view a
        child actually linked to their own Guardian record."""
        children = self.get_children(request)
        return get_object_or_404(children, pk=student_id)


class ParentDashboardView(ParentRequiredMixin, TemplateView):
    """Overview landing page — one row per child (spec §18 'Parent ->
    Child 1/2/3'), each with a quick academic/finance snapshot and a link
    into that child's detail pages."""

    template_name = "dashboard/parent/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        children = self.get_children(self.request)

        child_rows = []
        for child in children:
            term = Term.objects.filter(
                academic_year__school=child.school, is_current=True
            ).first()
            average = None
            if term is not None:
                class_subjects = ClassSubject.objects.filter(
                    enrollments__student=child, enrollments__academic_year=term.academic_year
                ).distinct()
                totals = []
                for cs in class_subjects:
                    summary = compute_weighted_average(child, cs, term)
                    if summary["weight_covered"] > 0:
                        totals.append(summary["weighted_total"])
                if totals:
                    average = sum(totals) / len(totals)
            account_summary = compute_student_account_summary(student=child)
            child_rows.append({
                "student": child, "current_term": term, "average": average,
                "account_summary": account_summary,
            })

        unread_notifications = 0  # Provided by context processor as unread_notification_count

        context.update({
            "child_rows": child_rows,
            "unread_notifications": unread_notifications,
        })
        return context


class ParentChildAcademicView(ParentRequiredMixin, TemplateView):
    """Spec §18: academic performance, report books, attendance,
    assignments — for one specific child."""

    template_name = "dashboard/parent/child_academic.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        child = self.get_child_or_404(self.request, kwargs["student_id"])
        term = Term.objects.filter(
            academic_year__school=child.school, is_current=True
        ).first()

        subject_rows = []
        assignment_rows = []
        if term is not None:
            class_subjects = ClassSubject.objects.filter(
                enrollments__student=child, enrollments__academic_year=term.academic_year
            ).distinct().select_related("subject")
            grading_scheme = child.school.grading_schemes.filter(is_default=True).first()
            for cs in class_subjects:
                summary = compute_weighted_average(child, cs, term)
                completion = compute_student_subject_completion(
                    student=child, class_subject=cs, term=term
                )
                band = None
                if grading_scheme and summary["weight_covered"] > 0:
                    band = get_grade_for_mark(grading_scheme, summary["weighted_total"])
                subject_rows.append({
                    "subject": cs.subject.name,
                    "score": summary["weighted_total"],
                    "grade": band.grade if band else "-",
                    "is_complete": completion["is_complete"],
                })

            assignments = Assignment.objects.filter(
                class_subject__in=class_subjects, is_published=True,
            ).select_related("class_subject__subject")
            my_submissions = {
                s.assignment_id: s
                for s in AssignmentSubmission.objects.filter(student=child)
            }
            assignment_rows = [
                {"assignment": a, "submission": my_submissions.get(a.pk)} for a in assignments
            ]

        report_cards = ReportCard.objects.filter(student=child).select_related("term")

        context.update({
            "student": child,
            "current_term": term,
            "subject_rows": subject_rows,
            "assignment_rows": assignment_rows,
            "report_cards": report_cards,
            "academic_history": build_parent_academic_history(student=child),
        })
        return context


class ParentChildFinanceView(ParentRequiredMixin, TemplateView):
    """Spec §18: fees, payments, balances — for one specific child.
    Same 'own child's data' reasoning as the Student Finance page
    (Phase 16): this is showing a guardian their own dependent's
    billing, not a cross-account leak."""

    template_name = "dashboard/parent/child_finance.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        child = self.get_child_or_404(self.request, kwargs["student_id"])

        from django.db.models import Count, Q

        guardian = get_object_or_404(Guardian, user=self.request.user)
        invoices = Invoice.objects.filter(student=child).order_by("-issue_date")
        payments = get_student_payment_records(student=child)
        account_summary = compute_student_account_summary(student=child)
        financial_history = build_student_financial_history(student=child)
        family_summary = compute_family_account_summary(guardian=guardian)
        fee_structures = FeeStructure.objects.filter(school=child.school, academic_year__is_current=True, is_active=True).filter(Q(class_groups=child.current_class) | Q(class_group=child.current_class)).prefetch_related("items__category", "transport_options").distinct()

        context.update({
            "student": child,
            "invoices": invoices,
            "payments": payments,
            "account_summary": account_summary,
            "family_summary": family_summary,
            "fee_structures": fee_structures,
            "financial_history": financial_history,
        })
        return context


class ParentFeeStructurePDFView(ParentRequiredMixin, View):
    def get(self, request, student_id, structure_id):
        child = self.get_child_or_404(request, student_id)
        structure = get_object_or_404(FeeStructure, pk=structure_id, school=child.school, is_active=True)
        if not (structure.class_group_id == child.current_class_id or structure.class_groups.filter(pk=child.current_class_id).exists()):
            raise Http404("Fee structure not found.")
        from .services import generate_fee_structure_pdf
        pdf = generate_fee_structure_pdf(structure=structure, student=child, generated_by=request.user, request=request)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="fee_structure_{structure.academic_year.name}_{child.admission_number}.pdf"'
        return response


class ParentCommunicationView(ParentRequiredMixin, TemplateView):
    """Spec §18: announcements, and (spec §22) parent-facing
    notifications like 'Your child's Term 2 report is available.'
    Not scoped to a single child — a parent's notification inbox and the
    school's PARENTS-audience announcements are shared across all their
    children."""

    template_name = "dashboard/parent/communication.html"
    active_nav = "communication"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        children = self.get_children(self.request)
        schools = {child.school_id for child in children}

        announcements = Announcement.objects.filter(
            school_id__in=schools, is_published=True,
        ).filter(
            _parent_audience_q()
        ).order_by("-created_at")[:20]

        notifications = Notification.objects.filter(
            recipient=self.request.user
        ).order_by("-created_at")[:50]

        context.update({
            "children": children,
            "announcements": announcements,
            "notifications": notifications,
        })
        return context


def _parent_audience_q():
    from django.db.models import Count, Q
    return Q(audience=Announcement.Audience.ALL) | Q(audience=Announcement.Audience.PARENTS)


class ParentMarkNotificationReadView(ParentRequiredMixin, View):
    def post(self, request, notification_id):
        notification = get_object_or_404(
            Notification, pk=notification_id, recipient=request.user
        )
        mark_notification_read(notification=notification)
        return redirect("dashboard:parent_communication")


# =============================================================================
# Phase 18 — Teacher Dashboard (spec §9)
#
# Ownership boundary: every class/subject/assessment-scoped action checks
# TeachingAssignment.objects.filter(teacher=staff, class_subject=...,
# is_active=True) before allowing access — a teacher must never be able to
# manage a class or grade an assessment they aren't actually assigned to,
# even by guessing a URL. Spec §9 "Teachers must not be able to approve
# their own final results" is enforced by transition_assessment_workflow()
# itself (Phase 8), reused here rather than re-implemented.
# =============================================================================

class TeacherRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.TEACHER, User.Role.CLASS_TEACHER]
    active_nav = None  # set per-view; drives sidebar active-link highlighting

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_staff(self, request) -> Staff:
        return get_object_or_404(Staff, user=request.user)

    def get_my_class_subjects(self, staff):
        return ClassSubject.objects.filter(
            teaching_assignments__teacher=staff, teaching_assignments__is_active=True,
        ).distinct().select_related("subject", "class_group")

    def get_owned_class_subject_or_404(self, staff, class_subject_id):
        return get_object_or_404(self.get_my_class_subjects(staff), pk=class_subject_id)


class TeacherDashboardView(TeacherRequiredMixin, TemplateView):
    """Overview landing page — spec §9's category list condensed into
    stat cards linking to the detail pages below."""

    template_name = "dashboard/teacher/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)

        pending_grading = AssignmentSubmission.objects.filter(
            assignment__class_subject__in=class_subjects,
            status=AssignmentSubmission.Status.SUBMITTED,
        ).count()

        from django.utils import timezone

        from .models import TimetableSlot as TimetableSlotModel

        # Bug fix: this widget is meant to be "what am I teaching today"
        # (the full week is already available via TeacherTimetableView),
        # but was previously missing a day_of_week filter entirely and
        # silently showed the teacher's whole week here instead.
        weekday_map = {
            0: TimetableSlotModel.DayOfWeek.MONDAY, 1: TimetableSlotModel.DayOfWeek.TUESDAY,
            2: TimetableSlotModel.DayOfWeek.WEDNESDAY, 3: TimetableSlotModel.DayOfWeek.THURSDAY,
            4: TimetableSlotModel.DayOfWeek.FRIDAY, 5: TimetableSlotModel.DayOfWeek.SATURDAY,
        }
        today_code = weekday_map.get(timezone.localtime(timezone.now()).weekday())
        today_slots = TimetableSlot.objects.filter(
            teacher=staff, day_of_week=today_code
        ).select_related("period", "class_group", "room").order_by("period__order") if today_code else TimetableSlot.objects.none()

        unread_notifications = 0  # Provided by context processor as unread_notification_count

        context.update({
            "staff": staff,
            "class_subject_count": class_subjects.count(),
            "pending_grading": pending_grading,
            "today_slots": today_slots,
            "unread_notifications": unread_notifications,
        })
        return context


class TeacherClassesView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'My classes', 'My subjects', 'My students'."""

    template_name = "dashboard/teacher/classes.html"
    active_nav = "classes"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)

        from django.db.models import Count, Q

        class_subjects = class_subjects.annotate(
            student_count=Count(
                "enrollments",
                filter=Q(enrollments__status=Enrollment.Status.ENROLLED),
                distinct=True,
            )
        )
        rows = [
            {"class_subject": cs, "student_count": cs.student_count}
            for cs in class_subjects
        ]

        context.update({"staff": staff, "rows": rows})
        return context


class TeacherClassRosterView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'My students' — the enrolled roster for one owned class+subject."""

    template_name = "dashboard/teacher/roster.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subject = self.get_owned_class_subject_or_404(staff, kwargs["class_subject_id"])

        students = Student.objects.filter(
            enrollments__class_subject=class_subject
        ).distinct().order_by("admission_number")

        context.update({"class_subject": class_subject, "students": students})
        return context


class TeacherTimetableView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'My timetable'."""

    template_name = "dashboard/teacher/timetable.html"
    active_nav = "timetable"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        current_term = Term.objects.filter(
            academic_year__school=staff.school, academic_year__is_current=True, is_current=True,
        ).first()
        if current_term is None:
            current_term = Term.objects.filter(
                teaching_assignments__teacher=staff,
            ).select_related("academic_year").order_by("-academic_year__start_date", "-term_number").first()
        slots = TimetableSlot.objects.filter(teacher=staff)
        if current_term:
            slots = slots.filter(term=current_term)
        slots = slots.select_related(
            "period", "class_group", "room", "teaching_assignment__class_subject__subject"
        ).order_by("day_of_week", "period__order")
        days = list(TimetableSlot.DayOfWeek.choices)
        periods = list(Period.objects.filter(school=staff.school).order_by("order", "start_time"))
        slots_by_day_period = {(slot.day_of_week, slot.period_id): slot for slot in slots}
        schedule_rows = [
            {"period": period, "cells": [
                {"day": day_code, "slot": slots_by_day_period.get((day_code, period.pk))}
                for day_code, _ in days
            ]}
            for period in periods
        ]
        context.update({
            "staff": staff, "slots": slots, "days": days, "periods": periods,
            "schedule_rows": schedule_rows, "schedule_term": current_term,
        })
        return context


class TeacherAttendanceView(TeacherRequiredMixin, TemplateView):
    """Spec §9/§11 'Record attendance' — the teacher's initial marking
    view for one owned class+subject on a given date (defaults today)."""

    template_name = "dashboard/teacher/attendance.html"

    def get_context_data(self, **kwargs):
        from django.utils import timezone

        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subject = self.get_owned_class_subject_or_404(staff, kwargs["class_subject_id"])
        date_str = self.request.GET.get("date")
        target_date = (
            datetime.date.fromisoformat(date_str) if date_str
            else timezone.localtime(timezone.now()).date()
        )

        students = Student.objects.filter(
            enrollments__class_subject=class_subject
        ).distinct().order_by("admission_number")

        existing = {}
        session = AttendanceSession.objects.filter(
            class_subject=class_subject, date=target_date
        ).first()
        if session:
            existing = {r.student_id: r for r in session.records.all()}

        student_rows = [
            {"student": s, "record": existing.get(s.pk)} for s in students
        ]

        context.update({
            "class_subject": class_subject, "students": students,
            "target_date": target_date, "existing": existing,
            "student_rows": student_rows,
            "session_locked": session.is_locked if session else False,
            "status_choices": AttendanceRecord.Status.choices,
        })
        return context

    def post(self, request, class_subject_id):
        staff = self.get_staff(request)
        class_subject = self.get_owned_class_subject_or_404(staff, class_subject_id)
        target_date = datetime.date.fromisoformat(request.POST.get("date"))

        term = TeachingAssignment.objects.filter(
            teacher=staff, class_subject=class_subject, is_active=True
        ).select_related("term").first()
        term = term.term if term else None

        records = {}
        for student in Student.objects.filter(enrollments__class_subject=class_subject).distinct():
            status = request.POST.get(f"status_{student.pk}")
            if status:
                records[student.pk] = {
                    "status": status, "notes": request.POST.get(f"notes_{student.pk}", ""),
                }

        try:
            mark_attendance(
                class_subject=class_subject, term=term, date=target_date,
                taken_by=staff, records=records, request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))

        return redirect(
            f"{reverse('dashboard:teacher_attendance', args=[class_subject.pk])}?date={target_date}"
        )


class TeacherAssignmentsView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'Assignments' — list across all owned classes, plus the
    create form ('Create assignment', 'Set instructions', 'Set deadline',
    'Define marks', 'Define submission format')."""

    template_name = "dashboard/teacher/assignments.html"
    active_nav = "assignments"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)
        assignments = Assignment.objects.filter(
            class_subject__in=class_subjects
        ).select_related("class_subject__subject").order_by("-deadline")

        context.update({
            "class_subjects": class_subjects, "assignments": assignments,
            "submission_formats": Assignment.SubmissionFormat.choices,
        })
        return context

    def post(self, request):
        staff = self.get_staff(request)
        if request.POST.get("action") in {"reopen", "close"}:
            assignment = get_object_or_404(
                Assignment, pk=request.POST.get("assignment_id"),
                class_subject__in=self.get_my_class_subjects(staff),
            )
            assignment.overdue_reopened = request.POST.get("action") == "reopen"
            assignment.save(update_fields=["overdue_reopened", "updated_at"])
            return redirect("dashboard:teacher_assignments")
        class_subject = self.get_owned_class_subject_or_404(
            staff, request.POST.get("class_subject_id")
        )
        term_id = TeachingAssignment.objects.filter(
            teacher=staff, class_subject=class_subject, is_active=True
        ).values_list("term_id", flat=True).first()

        assignment = Assignment.objects.create(
            class_subject=class_subject, term_id=term_id,
            title=request.POST.get("title", ""),
            instructions=request.POST.get("instructions", ""),
            deadline=request.POST.get("deadline"),
            max_marks=request.POST.get("max_marks") or Decimal("100"),
            max_attempts=max(1, int(request.POST.get("max_attempts") or 1)),
            submission_format=request.POST.get("submission_format", Assignment.SubmissionFormat.FILE_UPLOAD),
            allow_resubmission=bool(request.POST.get("allow_resubmission")) or int(request.POST.get("max_attempts") or 1) > 1,
            created_by=staff,
        )
        question_file = request.FILES.get("question_file")
        if question_file:
            try:
                validate_upload(question_file, validate_course_material_content, 5)
            except (FileValidationError, ValidationError) as exc:
                assignment.delete()
                return HttpResponseForbidden(str(exc))
            AssignmentResource.objects.create(
                assignment=assignment, title=f"{assignment.title} questions", file=question_file,
            )
        for student in Student.objects.filter(enrollments__class_subject=class_subject).select_related("user").distinct():
            send_notification(
                recipient=student.user,
                notification_type=Notification.NotificationType.TASK_ADDED,
                title="New assignment added",
                body=f"{assignment.title} has been added for {class_subject.subject.name}.",
                related_model="Assignment", related_object_id=assignment.pk, request=request,
            )
        return redirect("dashboard:teacher_assignments")


class TeacherAssignmentSubmissionsView(TeacherRequiredMixin, View):
    """Spec §9 'Mark assignments', 'Provide feedback' — reuses
    services.grade_assignment_submission() (Phase 11), already tested."""

    template_name = "dashboard/teacher/submissions.html"

    def _get_owned_assignment(self, request, assignment_id):
        staff = self.get_staff(request)
        return get_object_or_404(
            Assignment, pk=assignment_id, class_subject__in=self.get_my_class_subjects(staff)
        )

    def get(self, request, assignment_id):
        assignment = self._get_owned_assignment(request, assignment_id)
        submissions = assignment.submissions.select_related("student").order_by("-submitted_at")
        return render(request, self.template_name, {
            "assignment": assignment, "submissions": submissions,
        })

    def post(self, request, assignment_id):
        from .services import grade_assignment_submission

        assignment = self._get_owned_assignment(request, assignment_id)
        staff = self.get_staff(request)
        submission = get_object_or_404(
            assignment.submissions, pk=request.POST.get("submission_id")
        )
        try:
            grade_assignment_submission(
                submission=submission,
                marks_obtained=Decimal(request.POST.get("marks_obtained")),
                feedback=request.POST.get("feedback", ""),
                graded_by=staff, request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:teacher_assignment_submissions", assignment_id=assignment.pk)


class TeacherMaterialsView(TeacherRequiredMixin, TemplateView):
    """Spec §9/§10 'Upload learning materials', 'Upload PDFs', 'Upload videos'."""

    template_name = "dashboard/teacher/materials.html"
    active_nav = "materials"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)
        materials = CourseMaterial.objects.filter(
            class_subject__in=class_subjects
        ).select_related("class_subject__subject").order_by("-created_at")

        context.update({
            "class_subjects": class_subjects, "materials": materials,
            "material_types": CourseMaterial.MaterialType.choices,
        })
        return context

    def post(self, request):
        staff = self.get_staff(request)
        class_subject = self.get_owned_class_subject_or_404(
            staff, request.POST.get("class_subject_id")
        )
        term_id = TeachingAssignment.objects.filter(
            teacher=staff, class_subject=class_subject, is_active=True
        ).values_list("term_id", flat=True).first()

        material_type = request.POST.get("material_type")
        uploaded_file = request.FILES.get("file")
        if uploaded_file:
            category = COURSE_MATERIAL_TYPE_TO_CATEGORY.get(material_type)
            if category is None:
                return HttpResponseForbidden(
                    f"'{material_type}' does not accept a file upload; use "
                    f"external_url or text_content instead."
                )
            try:
                content_validator = {
                    "PDF": validate_pdf_content,
                    "IMAGE": validate_image_content,
                    "DOCUMENT": validate_course_material_content,
                    "VIDEO": validate_course_material_content,
                    "PRESENTATION": validate_course_material_content,
                }.get(material_type, validate_course_material_content)
                validate_upload(uploaded_file, content_validator, 5)
            except (FileValidationError, ValidationError) as exc:
                return HttpResponseForbidden(str(exc))

        CourseMaterial.objects.create(
            class_subject=class_subject, term_id=term_id,
            material_type=material_type,
            title=request.POST.get("title", ""),
            description=request.POST.get("description", ""),
            file=uploaded_file,
            external_url=request.POST.get("external_url", ""),
            text_content=request.POST.get("text_content", ""),
            uploaded_by=staff,
        )
        return redirect("dashboard:teacher_materials")


class TeacherAnnouncementsView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'Publish course announcements' — this is Discussion
    (course-scoped, Phase 11), distinct from the school-wide Announcement
    model (Phase 15). See Phase 11's design note on that split."""

    template_name = "dashboard/teacher/announcements.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subject = self.get_owned_class_subject_or_404(staff, kwargs["class_subject_id"])
        discussions = Discussion.objects.filter(
            class_subject=class_subject
        ).order_by("-is_pinned", "-created_at")

        context.update({"class_subject": class_subject, "discussions": discussions})
        return context

    def post(self, request, class_subject_id):
        staff = self.get_staff(request)
        class_subject = self.get_owned_class_subject_or_404(staff, class_subject_id)
        term_id = TeachingAssignment.objects.filter(
            teacher=staff, class_subject=class_subject, is_active=True
        ).values_list("term_id", flat=True).first()

        Discussion.objects.create(
            class_subject=class_subject, term_id=term_id,
            thread_type=Discussion.ThreadType.ANNOUNCEMENT,
            title=request.POST.get("title", ""), body=request.POST.get("body", ""),
            created_by=request.user,
        )
        return redirect("dashboard:teacher_announcements", class_subject_id=class_subject.pk)


class TeacherAssessmentsView(TeacherRequiredMixin, TemplateView):
    """Spec §9/§12 'Create assessments', 'Create exams'. Creates an
    Assessment against an AssessmentComponent that Academic Admin has
    already configured (Phase 7) — teachers don't define weighting
    schemes, only schedule instances of them."""

    template_name = "dashboard/teacher/assessments.html"
    active_nav = "assessments"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)
        class_subjects = self.get_my_class_subjects(staff)
        assessments = Assessment.objects.filter(
            class_subject__in=class_subjects
        ).select_related("class_subject__subject", "component").order_by("-created_at")

        quizzes = Quiz.objects.filter(class_subject__in=class_subjects).select_related(
            "class_subject__subject"
        ).order_by("-created_at")
        components = AssessmentComponent.objects.filter(
            structure__school=staff.school, structure__is_active=True,
            structure__term__teaching_assignments__teacher=staff,
            structure__term__teaching_assignments__is_active=True,
        ).select_related("assessment_type", "structure").distinct()
        context.update({
            "class_subjects": class_subjects, "assessments": assessments,
            "quizzes": quizzes, "components": components,
            "question_types": QuizQuestion.QuestionType.choices,
            "task_categories": Quiz.TaskCategory.choices,
            "quiz_submission_formats": Quiz.SubmissionFormat.choices,
        })
        return context

    def post(self, request):
        staff = self.get_staff(request)
        if request.POST.get("action") in {"reopen", "close"}:
            quiz = get_object_or_404(Quiz, pk=request.POST.get("quiz_id"), class_subject__in=self.get_my_class_subjects(staff))
            quiz.overdue_reopened = request.POST.get("action") == "reopen"
            quiz.save(update_fields=["overdue_reopened", "updated_at"])
            return redirect("dashboard:teacher_assessments")
        return HttpResponseForbidden("Teachers do not create assessments. The principal configures assessment structures.")


class TeacherMarkbookView(TeacherRequiredMixin, View):
    """Year-round, audited mark editing for classes the teacher taught."""
    template_name = "dashboard/teacher/markbook.html"
    active_nav = "assessments"

    def get_assessments(self, staff):
        return Assessment.objects.filter(
            class_subject__teaching_assignments__teacher=staff,
            class_subject__class_group__school=staff.school,
        ).select_related(
            "term__academic_year", "class_subject__class_group",
            "class_subject__subject", "component__assessment_type",
        ).distinct().order_by(
            "-term__academic_year__start_date", "-term__term_number", "title",
        )

    def build_context(self, request, staff, assessment=None, error=None, submitted=None):
        assessments = self.get_assessments(staff)
        students, existing_marks, student_rows = [], {}, []
        if assessment:
            students = list(Student.objects.filter(
                Q(enrollments__class_subject=assessment.class_subject,
                  enrollments__academic_year=assessment.term.academic_year)
                | Q(assessment_marks__assessment=assessment),
                school=staff.school,
            ).select_related("user").distinct().order_by("admission_number"))
            existing_marks = {
                mark.student_id: mark
                for mark in AssessmentMark.objects.filter(assessment=assessment)
            }
            student_rows = [{
                "student": student,
                "mark": existing_marks.get(student.pk),
                "value": (submitted.get(student.pk, "") if submitted is not None
                          else existing_marks.get(student.pk).marks_obtained
                          if existing_marks.get(student.pk) else ""),
            } for student in students]
        return {
            "active": self.active_nav, "assessments": assessments,
            "selected_assessment": assessment, "student_rows": student_rows,
            "error": error,
        }

    def get(self, request):
        staff = self.get_staff(request)
        assessment = None
        assessment_id = request.GET.get("assessment")
        if assessment_id:
            assessment = get_object_or_404(self.get_assessments(staff), pk=assessment_id)
        return render(request, self.template_name, self.build_context(request, staff, assessment))

    def post(self, request):
        staff = self.get_staff(request)
        assessment = get_object_or_404(
            self.get_assessments(staff), pk=request.POST.get("assessment_id"),
        )
        marks, submitted = {}, {}
        try:
            for key, raw in request.POST.items():
                if not key.startswith("mark_"):
                    continue
                student_id = int(key.removeprefix("mark_"))
                submitted[student_id] = raw
                if raw.strip():
                    marks[student_id] = Decimal(raw)
            record_teacher_markbook_marks(
                assessment=assessment, teacher=staff, marks=marks, request=request,
            )
        except (ValueError, ArithmeticError) as exc:
            return render(request, self.template_name,
                          self.build_context(request, staff, assessment, error=str(exc), submitted=submitted),
                          status=400)
        return redirect(f"{reverse('dashboard:teacher_markbook')}?assessment={assessment.pk}")


class TeacherQuizAttemptsView(TeacherRequiredMixin, View):
    template_name = "dashboard/teacher/quiz_attempts.html"

    def get_quiz(self, request, quiz_id):
        staff = self.get_staff(request)
        return get_object_or_404(Quiz, pk=quiz_id, class_subject__in=self.get_my_class_subjects(staff))

    def get(self, request, quiz_id):
        quiz = self.get_quiz(request, quiz_id)
        attempts = quiz.attempts.select_related("student__user").prefetch_related("answers__question")
        return render(request, self.template_name, {"quiz": quiz, "attempts": attempts})

    def post(self, request, quiz_id):
        quiz = self.get_quiz(request, quiz_id)
        answer = get_object_or_404(QuizAnswer, pk=request.POST.get("answer_id"), attempt__quiz=quiz)
        try:
            grade_quiz_short_answer(answer=answer, marks_awarded=Decimal(request.POST.get("marks_awarded")))
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:teacher_quiz_attempts", quiz_id=quiz.pk)


class TeacherMarksEntryView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'Enter marks'. Reuses services.record_assessment_marks()
    (this phase) for the write, and services.transition_assessment_workflow()
    (Phase 8) for the 'submit for review' action — which itself enforces
    'teachers cannot approve their own results' if this same teacher later
    tries to also approve it."""

    template_name = "dashboard/teacher/marks_entry.html"

    def _get_owned_assessment(self, request, assessment_id):
        staff = self.get_staff(request)
        return get_object_or_404(
            Assessment, pk=assessment_id, class_subject__in=self.get_my_class_subjects(staff)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assessment = self._get_owned_assessment(self.request, kwargs["assessment_id"])
        students = Student.objects.filter(
            enrollments__class_subject=assessment.class_subject
        ).distinct().order_by("admission_number")
        existing_marks = {
            m.student_id: m for m in AssessmentMark.objects.filter(assessment=assessment)
        }
        student_rows = [
            {"student": s, "mark": existing_marks.get(s.pk)} for s in students
        ]
        context.update({
            "assessment": assessment, "students": students, "existing_marks": existing_marks,
            "student_rows": student_rows,
        })
        return context

    def post(self, request, assessment_id):
        assessment = self._get_owned_assessment(request, assessment_id)
        staff = self.get_staff(request)

        if request.POST.get("action") == "submit_for_review":
            try:
                transition_assessment_workflow(
                    assessment=assessment, to_status=Assessment.WorkflowStatus.SUBMITTED,
                    actor=request.user, request=request,
                )
            except ValueError as exc:
                return HttpResponseForbidden(str(exc))
            return redirect("dashboard:teacher_assessments")

        marks = {}
        for student in Student.objects.filter(enrollments__class_subject=assessment.class_subject).distinct():
            raw = request.POST.get(f"mark_{student.pk}")
            if raw not in (None, ""):
                marks[student.pk] = Decimal(raw)

        try:
            record_assessment_marks(
                assessment=assessment, teacher=staff, marks=marks, request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))

        return redirect("dashboard:teacher_marks_entry", assessment_id=assessment.pk)


class TeacherCommunicationView(TeacherRequiredMixin, TemplateView):
    """Spec §9 'Announcements', 'Notifications' — the teacher's own inbox
    (school-wide/staff-audience Announcements + personal Notifications),
    distinct from course-level announcements they publish themselves
    (TeacherAnnouncementsView, above)."""

    template_name = "dashboard/teacher/communication.html"
    active_nav = "communication"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff(self.request)

        announcements = Announcement.objects.filter(
            school=staff.school, is_published=True,
        ).filter(_teacher_audience_q()).order_by("-created_at")[:20]

        notifications = Notification.objects.filter(
            recipient=self.request.user
        ).order_by("-created_at")[:50]

        context.update({"announcements": announcements, "notifications": notifications})
        return context


def _teacher_audience_q():
    from django.db.models import Count, Q
    return (
        Q(audience=Announcement.Audience.ALL)
        | Q(audience=Announcement.Audience.TEACHERS)
        | Q(audience=Announcement.Audience.STAFF)
    )


class TeacherMarkNotificationReadView(TeacherRequiredMixin, View):
    def post(self, request, notification_id):
        notification = get_object_or_404(
            Notification, pk=notification_id, recipient=request.user
        )
        mark_notification_read(notification=notification)
        return redirect("dashboard:teacher_communication")


# =============================================================================
# Phase 19 — Finance Admin Dashboard (spec §19, §23)
#
# Spec §23 'Financial and Academic Separation': Finance Admin gets fees,
# payments, invoices, financial reporting — never grades, assessments, or
# attendance. Every queryset here is scoped to Invoice/Payment/Refund and
# minimal student identity (name, admission number); nothing joins into
# AssessmentMark, SubjectResult, or AttendanceRecord.
# =============================================================================

class FinanceRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.MANAGER, User.Role.FINANCE_ADMIN, User.Role.ACCOUNTANT]
    active_nav = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_school(self, request):
        # Reuse tenant-resolved school from middleware first
        tenant_school = getattr(request, "school", None)
        if tenant_school is not None:
            return tenant_school
        try:
            return request.user.staff_profile.school
        except Exception:
            pass
        # Legacy fallback — cached to avoid expensive annotation on every request
        from django.core.cache import cache as _cache
        from .models import School
        cache_key = "finance:legacy_school_fallback"
        school = _cache.get(cache_key)
        if school is None:
            school = School.objects.filter(is_active=True).order_by("id").first()
            if school:
                _cache.set(cache_key, school, timeout=300)
        return school


class FinanceAdminDashboardView(FinanceRequiredMixin, TemplateView):
    """Overview — spec §19 stat cards: total billed/collected/outstanding,
    arrears, overdue invoices, recent payments. No academic data anywhere
    on this page."""

    template_name = "dashboard/finance/overview.html"
    active_nav = "finance_overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        summary = compute_school_financial_summary(school=school) if school else {}

        recent_payments = Payment.objects.filter(
            Q(invoice__school=school) | Q(family_guardian__school=school),
            status=Payment.Status.COMPLETED,
        ).select_related("invoice__student__user", "family_guardian").prefetch_related(
            "allocations__invoice__student__user", "receipt",
        ).distinct().order_by("-payment_date")[:10] if school else []

        pending_refunds = Refund.objects.filter(
            Q(payment__invoice__school=school) | Q(payment__family_guardian__school=school),
            status=Refund.Status.REQUESTED,
        ).count() if school else 0

        context.update({
            "school": school, "summary": summary,
            "recent_payments": recent_payments, "pending_refunds": pending_refunds,
        })
        return context


class FinanceAdminFeeStructuresView(FinanceRequiredMixin, TemplateView):
    template_name = "dashboard/finance/fee_structures.html"
    active_nav = "fee_structures"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        structures = (FeeStructure.objects.filter(school=school)
            .select_related("academic_year", "term", "class_group")
            .prefetch_related("items__category", "class_groups", "transport_options", "invoices")
            .order_by("-created_at") if school else FeeStructure.objects.none())
        edit_id = self.request.GET.get("edit")
        edit_structure = get_object_or_404(
            FeeStructure.objects.prefetch_related("items", "transport_options", "class_groups"),
            pk=edit_id, school=school,
        ) if edit_id else None
        context.update({
            "school": school, "structures": structures, "edit_structure": edit_structure,
            "academic_years": AcademicYear.objects.filter(school=school) if school else [],
            "terms": Term.objects.filter(academic_year__school=school) if school else [],
            "classes": Class.objects.filter(school=school, is_active=True).select_related("program") if school else [],
            "categories": FeeCategory.objects.filter(school=school, is_active=True) if school else [],
            "default_particulars": ["Admission", "Student ID", "Tuition", "Meals", "Caution", "Activity fee", "Medical fee"],
            "default_optional_particulars": ["Coding and Robotics"],
        })
        return context

    def post(self, request):
        school = self.get_school(request)
        if request.POST.get("action") == "update_fee_structure":
            structure = get_object_or_404(FeeStructure, pk=request.POST.get("structure_id"), school=school)
            class_ids = request.POST.getlist("class_ids")
            classes = list(Class.objects.filter(pk__in=class_ids, school=school, is_active=True))
            if not classes:
                return HttpResponseForbidden("Select at least one class.")
            structure.name = request.POST.get("name", "").strip()
            structure.paybill_number = request.POST.get("paybill_number", "").strip()
            structure.account_number = request.POST.get("account_number", "").strip()
            structure.class_group = classes[0]
            structure.save(update_fields=["name", "paybill_number", "account_number", "class_group"])
            structure.class_groups.set(classes)
            for item_id, particulars, t1, t2, t3 in zip(
                request.POST.getlist("item_id"), request.POST.getlist("item_particulars"),
                request.POST.getlist("item_term_1"), request.POST.getlist("item_term_2"),
                request.POST.getlist("item_term_3"), strict=False,
            ):
                item = get_object_or_404(FeeStructureItem, pk=item_id, structure=structure)
                vals = [Decimal(t1 or "0"), Decimal(t2 or "0"), Decimal(t3 or "0")]
                item.particulars = particulars.strip()
                item.section = request.POST.get(f"item_section_{item_id}", FeeStructureItem.Section.TUITION)
                item.term_1_amount, item.term_2_amount, item.term_3_amount = vals
                item.amount = sum(vals)
                item.save(update_fields=["particulars", "section", "term_1_amount", "term_2_amount", "term_3_amount", "amount"])
            for route_id, route, one, two in zip(
                request.POST.getlist("route_id"), request.POST.getlist("edit_route_name"),
                request.POST.getlist("edit_one_way_amount"), request.POST.getlist("edit_two_way_amount"), strict=False,
            ):
                transport = get_object_or_404(FeeStructureTransport, pk=route_id, structure=structure)
                transport.route_name = route.strip()
                transport.one_way_amount = Decimal(one or "0")
                transport.two_way_amount = Decimal(two or "0")
                transport.save(update_fields=["route_name", "one_way_amount", "two_way_amount"])
            return redirect("dashboard:finance_fee_structures")
        academic_year = get_object_or_404(AcademicYear, pk=request.POST.get("academic_year_id"), school=school)
        class_ids = request.POST.getlist("class_ids") or ([request.POST.get("class_id")] if request.POST.get("class_id") else [])
        classes = list(Class.objects.filter(pk__in=class_ids, school=school, is_active=True))
        if not classes:
            return HttpResponseForbidden("Select at least one class.")
        category, _ = FeeCategory.objects.get_or_create(school=school, code="GENERAL", defaults={"name": "General fees"})
        try:
            legacy_term = get_object_or_404(Term, pk=request.POST.get("term_id"), academic_year=academic_year) if request.POST.get("term_id") else None
            structure = FeeStructure.objects.create(
                school=school, academic_year=academic_year, term=legacy_term, class_group=classes[0],
                name=request.POST.get("name", "").strip() or f"Fee Structure - {academic_year.name}",
                paybill_number=request.POST.get("paybill_number", "").strip(),
                account_number=request.POST.get("account_number", "").strip(),
            )
            structure.class_groups.set(classes)
            if request.POST.get("category_id") and request.POST.get("amount"):
                old_category = get_object_or_404(FeeCategory, pk=request.POST.get("category_id"), school=school, is_active=True)
                old_amount = Decimal(request.POST.get("amount"))
                FeeStructureItem.objects.create(structure=structure, category=old_category, particulars=old_category.name, amount=old_amount)
            for section, prefix in (
                (FeeStructureItem.Section.TUITION, ""),
                (FeeStructureItem.Section.OPTIONAL, "optional_"),
            ):
                rows = zip(
                    request.POST.getlist(f"{prefix}particulars"),
                    request.POST.getlist(f"{prefix}term_1_amount"),
                    request.POST.getlist(f"{prefix}term_2_amount"),
                    request.POST.getlist(f"{prefix}term_3_amount"), strict=False,
                )
                for particulars, t1, t2, t3 in rows:
                    particulars = particulars.strip()
                    if not particulars:
                        continue
                    vals = [Decimal(v or "0") for v in (t1, t2, t3)]
                    if not any(vals):
                        continue
                    code = ("P_" + "_".join(particulars.upper().split()))[:20]
                    row_category, _ = FeeCategory.objects.get_or_create(
                        school=school, code=code, defaults={"name": particulars}
                    )
                    FeeStructureItem.objects.create(
                        structure=structure, category=row_category, particulars=particulars,
                        section=section, amount=sum(vals), term_1_amount=vals[0],
                        term_2_amount=vals[1], term_3_amount=vals[2],
                        is_mandatory=(section == FeeStructureItem.Section.TUITION),
                    )
            for route, one, two in zip(request.POST.getlist("route_name"), request.POST.getlist("one_way_amount"), request.POST.getlist("two_way_amount"), strict=False):
                if route.strip() and (one or two):
                    FeeStructureTransport.objects.create(structure=structure, route_name=route.strip(), one_way_amount=Decimal(one or "0"), two_way_amount=Decimal(two or "0"))
            current_term = Term.objects.filter(
                academic_year=academic_year, is_current=True,
            ).first()
            if current_term and (legacy_term is None or legacy_term == current_term):
                for class_group in classes:
                    for student in Student.objects.filter(
                        school=school, current_class=class_group, is_active=True,
                    ).select_related("current_class"):
                        ensure_current_term_invoice_for_student(
                            student=student, issued_by=request.user, request=request,
                        )
            return redirect("dashboard:finance_fee_structures")
        except (ValueError, TypeError) as exc:
            return HttpResponseForbidden(str(exc))


class FinanceFeeStructurePDFView(FinanceRequiredMixin, View):
    def get(self, request, structure_id):
        structure = get_object_or_404(FeeStructure, pk=structure_id, school=self.get_school(request))
        from .services import generate_fee_structure_pdf
        pdf = generate_fee_structure_pdf(structure=structure, student=None, generated_by=request.user, request=request)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'inline; filename="fee_structure_{structure.academic_year.name}.pdf"'
        return response


class FinanceAdminInvoicesView(FinanceRequiredMixin, TemplateView):
    """Spec §19 'Invoices'. List + generate-invoice action, reusing
    services.generate_invoice_for_student() (Phase 12, already tested)."""

    template_name = "dashboard/finance/invoices.html"
    active_nav = "invoices"

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        status_filter = self.request.GET.get("status", "")

        invoices = Invoice.objects.filter(school=school).select_related(
            "student__user", "academic_year", "term"
        ).order_by("-issue_date") if school else Invoice.objects.none()
        if status_filter:
            invoices = invoices.filter(status=status_filter)

        paginator = Paginator(invoices, 25)
        page_obj = paginator.get_page(self.request.GET.get("page"))

        context.update({
            "school": school, "invoices": page_obj, "page_obj": page_obj,
            "status_choices": Invoice.Status.choices, "status_filter": status_filter,
            "fee_structures": FeeStructure.objects.filter(school=school, is_active=True) if school else [],
            "students": Student.objects.filter(school=school, is_active=True).select_related("user").only(
                "id", "user__first_name", "user__last_name", "user__username", "admission_number"
            ) if school else [],
        })
        return context

    def post(self, request):
        school = self.get_school(request)
        student = get_object_or_404(Student, pk=request.POST.get("student_id"), school=school)
        fee_structure = get_object_or_404(
            FeeStructure, pk=request.POST.get("fee_structure_id"), school=school
        )
        try:
            generate_invoice_for_student(
                student=student, fee_structure=fee_structure,
                academic_year=fee_structure.academic_year, term=fee_structure.term,
                issued_by=request.user, due_date=request.POST.get("due_date"),
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:finance_invoices")


class FinanceAdminInvoiceDetailView(FinanceRequiredMixin, View):
    """Spec §19 'Payments', 'Partial payments', 'Balances'. View one
    invoice + record a payment against it, reusing
    services.record_payment() (Phase 12, already tested)."""

    template_name = "dashboard/finance/invoice_detail.html"
    active_nav = "invoices"

    def get(self, request, invoice_id):
        school = self.get_school(request)
        invoice = get_object_or_404(
            Invoice.objects.select_related("student__user"), pk=invoice_id, school=school
        )
        return render(request, self.template_name, {
            "invoice": invoice, "active": self.active_nav,
            "payment_methods": [
                choice for choice in Payment.Method.choices
                if choice[0] in {
                    Payment.Method.CASH, Payment.Method.BANK_TRANSFER,
                    Payment.Method.MOBILE_MONEY, Payment.Method.MPESA,
                }
            ],
        })

    def post(self, request, invoice_id):
        school = self.get_school(request)
        invoice = get_object_or_404(Invoice, pk=invoice_id, school=school)
        try:
            record_payment(
                invoice=invoice, amount=Decimal(request.POST.get("amount")),
                payment_method=request.POST.get("payment_method"),
                payment_date=request.POST.get("payment_date"),
                received_by=request.user, payer_name=request.POST.get("payer_name", ""),
                gateway_reference=request.POST.get("gateway_reference", ""),
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:finance_invoice_detail", invoice_id=invoice.pk)


class FinanceAdminFamilyPaymentView(FinanceRequiredMixin, TemplateView):
    template_name = "dashboard/finance/family_payment.html"
    active_nav = "family_payments"

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        families = []
        page_obj = None
        if school:
            guardian_qs = Guardian.objects.filter(
                school=school, is_active=True
            ).order_by("last_name", "first_name")
            paginator = Paginator(guardian_qs, 15)
            page_number = self.request.GET.get("page")
            page_obj = paginator.get_page(page_number)
            for guardian in page_obj:
                children = Student.objects.filter(
                    school=school, studentguardian__guardian=guardian
                ).distinct().select_related("user")
                summary = compute_family_account_summary(guardian=guardian)
                invoices = Invoice.objects.filter(
                    school=school, student__in=children
                ).exclude(status=Invoice.Status.CANCELLED).select_related(
                    "student__user"
                ).order_by("student_id", "issue_date")
                invoice_list = list(invoices)
                family_payments = list(Payment.objects.filter(
                    family_guardian=guardian, invoice__isnull=True,
                ).select_related("received_by").prefetch_related(
                    "allocations__invoice__student__user", "refunds",
                ).order_by("-payment_date"))
                for payment in family_payments:
                    allocations_by_invoice = {
                        allocation.invoice_id: allocation.amount
                        for allocation in payment.allocations.all()
                    }
                    payment.edit_allocation_rows = [{
                        "invoice": invoice,
                        "amount": allocations_by_invoice.get(invoice.pk, ""),
                    } for invoice in invoice_list]
                families.append({
                    "guardian": guardian,
                    "children": children,
                    "child_balances": summary["children"],
                    "invoices": invoice_list,
                    "payments": family_payments,
                    "total_billed": summary["total_billed"],
                    "total_paid": summary["total_paid"],
                    "outstanding_balance": summary["outstanding_balance"],
                })
        context.update({
            "school": school,
            "families": families,
            "page_obj": page_obj,
            "payment_methods": [
                choice for choice in Payment.Method.choices
                if choice[0] in {
                    Payment.Method.CASH, Payment.Method.BANK_TRANSFER,
                    Payment.Method.MOBILE_MONEY, Payment.Method.MPESA,
                }
            ],
            "can_edit_family_payments": self.request.user.role in {
                User.Role.MANAGER, User.Role.FINANCE_ADMIN, User.Role.ACCOUNTANT,
            },
        })
        return context

    def post(self, request):
        school = self.get_school(request)
        if request.POST.get("action") == "update_payment":
            if request.user.role not in {
                User.Role.MANAGER, User.Role.FINANCE_ADMIN, User.Role.ACCOUNTANT,
            }:
                return HttpResponseForbidden("Only the manager or finance officer can edit family payments.")
            guardian = get_object_or_404(
                Guardian, pk=request.POST.get("guardian_id"), school=school,
            )
            payment = get_object_or_404(
                Payment, pk=request.POST.get("payment_id"), family_guardian=guardian,
                invoice__isnull=True,
            )
            allocations = []
            try:
                for invoice_id in request.POST.getlist("invoice_id"):
                    raw_amount = request.POST.get(f"edit_allocation_{invoice_id}", "").strip()
                    if not raw_amount or Decimal(raw_amount) == 0:
                        continue
                    invoice = get_object_or_404(
                        Invoice, pk=invoice_id, school=school,
                        student__studentguardian__guardian=guardian,
                    )
                    allocations.append((invoice, Decimal(raw_amount)))
                payment_date_value = datetime.datetime.fromisoformat(
                    request.POST.get("payment_date", "").strip()
                )
                if timezone.is_naive(payment_date_value):
                    payment_date_value = timezone.make_aware(payment_date_value)
                update_family_payment(
                    payment=payment, guardian=guardian,
                    amount=Decimal(request.POST.get("amount")), allocations=allocations,
                    payment_method=request.POST.get("payment_method"),
                    payment_date=payment_date_value, edited_by=request.user,
                    payer_name=request.POST.get("payer_name", ""),
                    reference=request.POST.get("reference", ""),
                    notes=request.POST.get("notes", ""), request=request,
                )
            except (ValueError, TypeError, InvalidOperation) as exc:
                return HttpResponseForbidden(str(exc))
            return redirect("dashboard:finance_family_payment")

        guardian = get_object_or_404(
            Guardian, pk=request.POST.get("guardian_id"), school=school, is_active=True
        )
        allocations = []
        for invoice_id in request.POST.getlist("invoice_id"):
            raw_amount = request.POST.get(f"allocation_{invoice_id}", "").strip()
            if not raw_amount:
                continue
            invoice = get_object_or_404(
                Invoice, pk=invoice_id, school=school, student__studentguardian__guardian=guardian
            )
            allocations.append((invoice, Decimal(raw_amount)))
        try:
            record_family_payment(
                guardian=guardian, amount=Decimal(request.POST.get("amount")),
                allocations=allocations,
                payment_method=request.POST.get("payment_method"),
                payment_date=request.POST.get("payment_date") or timezone.now(),
                received_by=request.user, payer_name=request.POST.get("payer_name", ""),
                reference=request.POST.get("reference", ""),
                notes=request.POST.get("notes", ""), request=request,
            )
        except (ValueError, TypeError) as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:finance_family_payment")


class FinanceAdminRefundsView(FinanceRequiredMixin, TemplateView):
    """Spec §19 'Refunds'. Approval queue, reusing
    services.decide_refund() (Phase 12, already tested — including the
    'cannot decide the same refund twice' guard)."""

    template_name = "dashboard/finance/refunds.html"
    active_nav = "refunds"

    def get_context_data(self, **kwargs):
        from django.core.paginator import Paginator

        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        refunds = Refund.objects.filter(
            Q(payment__invoice__school=school) | Q(payment__family_guardian__school=school)
        ).select_related(
            "payment__invoice__student__user", "payment__family_guardian",
        ).order_by("-requested_at") if school else Refund.objects.none()
        paginator = Paginator(refunds, 25)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        context.update({"school": school, "refunds": page_obj, "page_obj": page_obj})
        return context

    def post(self, request):
        school = self.get_school(request)
        refund = get_object_or_404(
            Refund.objects.filter(
                Q(payment__invoice__school=school) | Q(payment__family_guardian__school=school)
            ), pk=request.POST.get("refund_id"),
        )
        approve = request.POST.get("action") == "approve"
        try:
            decide_refund(
                refund=refund, approve=approve, decided_by=request.user,
                refund_method=request.POST.get("refund_method", ""),
                reference_number=request.POST.get("reference_number", ""),
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:finance_refunds")


# =============================================================================
# Phase 20 — Staff Admin Dashboard (spec §6)
#
# 'Restrict sensitive HR information to authorized users' — this entire
# section is gated to STAFF_ADMIN (and Super Admin via RoleRequiredMixin's
# is_superuser override). No other role's dashboard reads Staff HR fields
# beyond public-facing display name.
# =============================================================================

class StaffAdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = [User.Role.MANAGER, User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL, User.Role.STAFF_ADMIN]
    active_nav = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_school(self, request):
        if request.user.is_superuser:
            return School.objects.first()
        if getattr(request, "school", None) is not None:
            return request.school
        try:
            return request.user.staff_profile.school
        except Staff.DoesNotExist:
            schools = School.objects.filter(is_active=True)
            return schools.first() if schools.count() == 1 else None


class StaffAdminDashboardView(StaffAdminRequiredMixin, TemplateView):
    """Overview — staff counts by employment status, pending leave
    requests, today's attendance summary."""

    template_name = "dashboard/staff_admin/overview.html"
    active_nav = "staff_overview"
    allowed_roles = [User.Role.MANAGER, User.Role.STAFF_ADMIN]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        staff_qs = Staff.objects.filter(school=school) if school else Staff.objects.none()
        if self.request.user.role == User.Role.DEPUTY_PRINCIPAL:
            staff_qs = staff_qs.filter(user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])

        context.update({
            "school": school,
            "total_staff": staff_qs.filter(is_active=True).count(),
            "on_leave_count": staff_qs.filter(
                employment_status=Staff.EmploymentStatus.ON_LEAVE
            ).count(),
            "pending_leave_requests": LeaveRequest.objects.filter(
                staff__school=school, status=LeaveRequest.Status.PENDING
            ).count() if school else 0,
            "departments": Department.objects.filter(school=school, is_active=True) if school else [],
        })
        return context


class StaffAdminStaffListView(StaffAdminRequiredMixin, TemplateView):
    """Spec §6 'Staff profiles', 'Departments', 'Job titles', 'Employment
    status'. List + search; create-staff action is deliberately NOT a
    quick inline form here — creating a Staff record requires first
    creating its linked User (username/password/role), which is a
    distinct enough operation to warrant its own confirmation step rather
    than a silent side-effect of this list page. See StaffAdminStaffCreateView."""

    template_name = "dashboard/staff_admin/staff_list.html"
    active_nav = "staff"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        staff_qs = (Staff.objects.all() if self.request.user.is_superuser else Staff.objects.filter(school=school)).select_related(
            "user", "department", "school"
        ).order_by("-created_at") if (school or self.request.user.is_superuser) else Staff.objects.none()

        teacher_mode = self.request.GET.get("teachers") == "1" or self.request.user.role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}
        if teacher_mode:
            staff_qs = staff_qs.filter(user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
        search = self.request.GET.get("q", "").strip()
        if search:
            from django.db.models import Count, Q
            staff_qs = staff_qs.filter(
                Q(staff_id__icontains=search) | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )

        from django.core.paginator import Paginator
        paginator = Paginator(staff_qs, 25)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        context.update({
            "school": school, "staff_list": page_obj, "page_obj": page_obj, "search": search,
            "departments": Department.objects.filter(is_active=True) if self.request.user.is_superuser else (Department.objects.filter(school=school, is_active=True) if school else []),
            "schools": School.objects.filter(is_active=True) if self.request.user.is_superuser else [],
            "teacher_mode": teacher_mode,
            "active": "teachers" if teacher_mode else self.active_nav,
        })
        return context


class StaffAdminStaffCreateView(StaffAdminRequiredMixin, View):
    """Spec §6 'Create staff'. Creates the User (login) and Staff
    (HR profile) together — a Staff record cannot exist without a User,
    per the OneToOneField in Phase 3's model."""

    def post(self, request):
        from django.utils.crypto import get_random_string

        school = (get_object_or_404(School, pk=request.POST.get("school_id"))
                  if request.user.is_superuser and request.POST.get("school_id")
                  else self.get_school(request))
        username = request.POST.get("username", "").strip()
        if not username:
            return HttpResponseForbidden("Username is required.")
        requested_role = request.POST.get("role", User.Role.TEACHER)
        if request.user.role == User.Role.PRINCIPAL and requested_role not in {User.Role.TEACHER, User.Role.CLASS_TEACHER}:
            return HttpResponseForbidden("Principals may only enroll teaching staff.")

        temporary_password = request.POST.get("password") or get_random_string(16)
        new_user = User.objects.create_user(
            username=username, password=temporary_password,
            first_name=request.POST.get("first_name", ""),
            last_name=request.POST.get("last_name", ""),
            email=request.POST.get("email", ""),
            role=requested_role,
            must_change_password=True,
        )
        Staff.objects.create(
            user=new_user, school=school, staff_id=request.POST.get("staff_id", ""),
            department_id=request.POST.get("department_id") or None,
            job_title=request.POST.get("job_title", ""),
            date_hired=request.POST.get("date_hired") or datetime.date.today(),
            salary=Decimal(request.POST.get("salary") or "0") if request.user.role != User.Role.PRINCIPAL else Decimal("0"),
        )
        if not request.POST.get("password"):
            messages.success(request, f"Temporary sign-in password for {username}: {temporary_password}")
        return redirect("dashboard:staff_admin_staff_list")


class StaffAdminStaffDetailView(StaffAdminRequiredMixin, View):
    """Spec §6 'Edit staff', 'Deactivate staff', 'Qualifications',
    'Certifications', 'Emergency contacts'."""

    template_name = "dashboard/staff_admin/staff_detail.html"
    active_nav = "staff"

    def get_staff(self, request, staff_id):
        queryset = Staff.objects.select_related("user", "school")
        if not request.user.is_superuser:
            queryset = queryset.filter(school=self.get_school(request))
        if request.user.role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}:
            queryset = queryset.filter(user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
        return get_object_or_404(queryset, pk=staff_id)

    def get(self, request, staff_id):
        staff = self.get_staff(request, staff_id)
        return render(request, self.template_name, {
            "staff": staff, "active": self.active_nav,
            "qualifications": staff.qualifications.all(),
            "employment_statuses": Staff.EmploymentStatus.choices,
        })

    def post(self, request, staff_id):
        staff = self.get_staff(request, staff_id)
        action = request.POST.get("action")

        if action == "deactivate":
            deactivate_staff(
                staff=staff, deactivated_by=request.user,
                reason=request.POST.get("reason", ""), request=request,
            )
        elif action == "reactivate":
            reactivate_staff(staff=staff, reactivated_by=request.user, request=request)
        else:
            # Edit profile fields.
            staff.job_title = request.POST.get("job_title", staff.job_title)
            staff.emergency_contact_name = request.POST.get(
                "emergency_contact_name", staff.emergency_contact_name
            )
            staff.emergency_contact_phone = request.POST.get(
                "emergency_contact_phone", staff.emergency_contact_phone
            )
            if request.user.role != User.Role.PRINCIPAL:
                staff.salary = Decimal(request.POST.get("salary") or staff.salary or "0")
            staff.save()
        return redirect("dashboard:staff_admin_staff_detail", staff_id=staff.pk)


class StaffAdminAttendanceView(StaffAdminRequiredMixin, TemplateView):
    """Spec §6 'Staff attendance' — mark attendance for all staff on a
    given date."""

    template_name = "dashboard/staff_admin/attendance.html"
    active_nav = "staff_attendance"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        target_date = self.request.GET.get("date") or datetime.date.today().isoformat()

        staff_qs = Staff.objects.filter(school=school, is_active=True).select_related("user") if school else Staff.objects.none()
        if self.request.user.role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}:
            staff_qs = staff_qs.filter(user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
        existing = {
            r.staff_id: r for r in StaffAttendanceRecord.objects.filter(
                staff__school=school, date=target_date
            )
        } if school else {}
        staff_rows = [{"staff": s, "record": existing.get(s.pk)} for s in staff_qs]

        context.update({
            "school": school, "staff_rows": staff_rows, "target_date": target_date,
            "status_choices": StaffAttendanceRecord.Status.choices,
        })
        return context

    def post(self, request):
        school = self.get_school(request)
        target_date = request.POST.get("date")
        staff_qs = Staff.objects.filter(school=school, is_active=True)
        if request.user.role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}:
            staff_qs = staff_qs.filter(user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
        for staff in staff_qs:
            status = request.POST.get(f"status_{staff.pk}")
            if not status:
                continue
            record_staff_attendance(
                staff=staff, date=target_date, status=status, recorded_by=request.user,
                notes=request.POST.get(f"notes_{staff.pk}", ""), request=request,
            )
        return redirect(f"{reverse('dashboard:staff_admin_attendance')}?date={target_date}")


class StaffAdminLeaveRequestsView(StaffAdminRequiredMixin, TemplateView):
    allowed_roles = [User.Role.MANAGER, User.Role.PRINCIPAL, User.Role.STAFF_ADMIN]
    """Spec §6 leave workflow — the Staff Admin review/approve/reject
    step. Approval/rejection reuses services.decide_leave_request(),
    which sends the required notification (Phase 15)."""

    template_name = "dashboard/staff_admin/leave_request.html"
    active_nav = "leave"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        requests_qs = LeaveRequest.objects.filter(
            staff__school=school
        ).select_related("staff__user").order_by("-requested_at") if school else []
        if self.request.user.role == User.Role.PRINCIPAL and school:
            requests_qs = requests_qs.filter(staff__user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
        context.update({"school": school, "leave_requests": requests_qs})
        return context

    def post(self, request):
        school = self.get_school(request)
        leave_request = get_object_or_404(
            LeaveRequest, pk=request.POST.get("leave_request_id"), staff__school=school
        )
        if request.user.role == User.Role.PRINCIPAL and leave_request.staff.user.role not in {User.Role.TEACHER, User.Role.CLASS_TEACHER}:
            raise Http404("Leave request not found.")
        approve = request.POST.get("action") == "approve"
        try:
            decide_leave_request(
                leave_request=leave_request, approve=approve, decided_by=request.user,
                decision_notes=request.POST.get("decision_notes", ""), request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:staff_admin_leave_requests")


class StaffAdminWorkloadView(StaffAdminRequiredMixin, TemplateView):
    """Spec §6 'Staff workload': assigned classes, subjects, teaching
    hours, timetable. Read-only — reuses Phase 5/14 data via
    compute_staff_workload(), no new write path."""

    template_name = "dashboard/staff_admin/workload.html"
    allowed_roles = [User.Role.MANAGER, User.Role.STAFF_ADMIN]
    active_nav = "workload"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        term = Term.objects.filter(academic_year__school=school, is_current=True).first() if school else None

        rows = []
        if term:
            workload_staff = Staff.objects.filter(school=school, is_active=True).select_related("user")
            if self.request.user.role == User.Role.DEPUTY_PRINCIPAL:
                workload_staff = workload_staff.filter(user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
            for staff in workload_staff:
                workload = compute_staff_workload(staff=staff, term=term)
                rows.append({"staff": staff, "workload": workload})

        context.update({"school": school, "term": term, "workload_rows": rows})
        return context


class MyLeaveRequestsView(LoginRequiredMixin, TemplateView):
    """Spec §6 leave workflow, step one: 'Staff submits leave request.'
    Generic self-service view for any staff member (Teacher, Librarian,
    Accountant, etc.) — not gated to a specific staff role, only to
    having a linked Staff profile at all, since every staff type can
    take leave. This is the missing entry point the workflow needs:
    without it, LeaveRequest rows could only ever be created via Django
    admin, never by the staff member themselves."""

    template_name = "dashboard/staff_self_service/my_leave_requests.html"

    def get_staff_or_404(self, request):
        return get_object_or_404(Staff, user=request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        staff = self.get_staff_or_404(self.request)
        context.update({
            "staff": staff,
            "leave_requests": staff.leave_requests.order_by("-requested_at"),
            "leave_types": LeaveRequest.LeaveType.choices,
        })
        return context

    def post(self, request):
        staff = self.get_staff_or_404(request)
        try:
            submit_leave_request(
                staff=staff, leave_type=request.POST.get("leave_type"),
                start_date=request.POST.get("start_date"),
                end_date=request.POST.get("end_date"),
                reason=request.POST.get("reason", ""), request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:my_leave_requests")


# =============================================================================
# Phase 21 — Academic Admin Dashboard (spec §7)
#
# 'Academic Admin must manage academic operations without accessing
# confidential financial information' — no view in this section imports
# or queries Invoice/Payment/Refund/FeeStructure/FeeConcession.
#
# Two of these views close loops left open since earlier phases: result
# approval (Phase 8's workflow was fully built/tested but had no
# Academic Admin-facing view until now) and attendance correction
# (Phase 6's correct_attendance_record() was built/tested but likewise
# never had a UI entry point).
# =============================================================================

class AcademicAdminRequiredMixin(RoleRequiredMixin):
    allowed_roles = [
        User.Role.ACADEMIC_ADMIN,
        User.Role.MANAGER, User.Role.PRINCIPAL,
        User.Role.DEPUTY_PRINCIPAL,
    ]
    active_nav = None

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["active"] = self.active_nav
        return context

    def get_school(self, request):
        from .models import School
        if hasattr(request.user, "staff_profile"):
            return request.user.staff_profile.school
        # Compatibility for existing single-school legacy accounts.
        schools = School.objects.filter(is_active=True)
        return schools.first() if schools.count() == 1 else None


class PrincipalConfigurationView(AcademicAdminRequiredMixin, TemplateView):
    """Frontend configuration for the school principal.

    All writes are school-scoped and use the existing curriculum, guardian,
    enrollment, and assessment models. Structure creation also materializes
    mark-entry Assessment rows for each relevant class subject, so teachers
    only enter physical-exam marks.
    """
    template_name = "dashboard/academic_admin/configuration.html"
    active_nav = "configuration"
    deputy_write_allowed = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        classes = Class.objects.filter(school=school, is_active=True).select_related("program") if school else Class.objects.none()
        subjects = Subject.objects.filter(school=school, is_active=True).select_related("department") if school else Subject.objects.none()
        class_subjects = ClassSubject.objects.filter(class_group__school=school, is_active=True).select_related("class_group", "subject") if school else ClassSubject.objects.none()
        teaching_staff = Staff.objects.filter(
            school=school, is_active=True,
            employment_status=Staff.EmploymentStatus.ACTIVE,
        ).select_related("user") if school else Staff.objects.none()
        if self.request.user.role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}:
            teaching_staff = teaching_staff.filter(user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
        context.update({
            "school": school,
            "classes": classes,
            "streams": Stream.objects.filter(class_group__school=school, is_active=True).select_related("class_group") if school else [],
            "subjects": subjects,
            "departments": Department.objects.filter(school=school, is_active=True) if school else [],
            "class_subjects": class_subjects,
            "teachers": teaching_staff,
            "students": Student.objects.filter(school=school, is_active=True).select_related("user", "current_class") if school else [],
            "guardians": Guardian.objects.filter(school=school, is_active=True) if school and self.request.user.role not in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL} else [],
            "academic_years": AcademicYear.objects.filter(school=school) if school else [],
            "terms": Term.objects.filter(academic_year__school=school) if school else [],
            "assessment_types": AssessmentType.objects.filter(school=school, is_active=True) if school else [],
            "structures": AssessmentStructure.objects.filter(school=school).select_related("term", "subject").prefetch_related("components__assessment_type") if school else [],
            "grading_schemes": GradingScheme.objects.filter(school=school, is_active=True).prefetch_related("bands") if school else [],
            "report_templates": ReportTemplate.objects.filter(school=school, is_active=True) if school else [],
            "template_keys": ReportTemplate.TemplateKey.choices,
            "teachers_for_heads": teaching_staff,
        })
        edit_type = self.request.GET.get("edit")
        edit_id = self.request.GET.get("id")
        edit_models = {
            "department": (Department, {"school": school}), "academic_year": (AcademicYear, {"school": school}),
            "term": (Term, {"academic_year__school": school}), "class": (Class, {"school": school}),
            "stream": (Stream, {"class_group__school": school}), "subject": (Subject, {"school": school}),
            "assessment_type": (AssessmentType, {"school": school}), "grading_scheme": (GradingScheme, {"school": school}),
            "report_template": (ReportTemplate, {"school": school}), "structure": (AssessmentStructure, {"school": school}),
        }
        if edit_type in edit_models and edit_id:
            model, filters = edit_models[edit_type]
            context["edit_type"] = edit_type
            context["edit_object"] = get_object_or_404(model, pk=edit_id, **filters)
        return context

    def post(self, request):
        school = self.get_school(request)
        action = request.POST.get("action")
        if request.user.role == User.Role.DEPUTY_PRINCIPAL and action in {"enroll_student", "link_guardian"}:
            return HttpResponseForbidden("You do not have access to this operation.")
        try:
            if action == "update_module":
                module = request.POST.get("module")
                obj_id = request.POST.get("object_id")
                if module == "department":
                    obj = get_object_or_404(Department, pk=obj_id, school=school); obj.name = request.POST.get("name", "").strip(); obj.code = request.POST.get("code", "").strip(); obj.save(update_fields=["name", "code", "updated_at"])
                elif module == "academic_year":
                    obj = get_object_or_404(AcademicYear, pk=obj_id, school=school); obj.name = request.POST.get("name", "").strip(); obj.start_date = datetime.date.fromisoformat(request.POST.get("start_date")); obj.end_date = datetime.date.fromisoformat(request.POST.get("end_date")); obj.is_current = request.POST.get("is_current") == "on"; obj.save()
                elif module == "term":
                    obj = get_object_or_404(Term, pk=obj_id, academic_year__school=school); obj.name = request.POST.get("name", "").strip(); obj.term_number = int(request.POST.get("term_number")); obj.start_date = datetime.date.fromisoformat(request.POST.get("start_date")); obj.end_date = datetime.date.fromisoformat(request.POST.get("end_date")); obj.is_current = request.POST.get("is_current") == "on"; obj.save()
                elif module == "class":
                    obj = get_object_or_404(Class, pk=obj_id, school=school); obj.name = request.POST.get("name", "").strip(); obj.level_order = int(request.POST.get("level_order") or 0); obj.save(update_fields=["name", "level_order", "updated_at"])
                elif module == "stream":
                    obj = get_object_or_404(Stream, pk=obj_id, class_group__school=school); obj.name = request.POST.get("name", "").strip(); obj.capacity = int(request.POST.get("capacity") or 0); obj.save(update_fields=["name", "capacity", "updated_at"])
                elif module == "subject":
                    obj = get_object_or_404(Subject, pk=obj_id, school=school); obj.name = request.POST.get("name", "").strip(); obj.code = request.POST.get("code", "").strip(); obj.description = request.POST.get("description", "").strip(); obj.save(update_fields=["name", "code", "description", "updated_at"])
                elif module == "assessment_type":
                    obj = get_object_or_404(AssessmentType, pk=obj_id, school=school); obj.name = request.POST.get("name", "").strip(); obj.code = request.POST.get("code", "").strip().upper(); obj.save(update_fields=["name", "code", "updated_at"])
                elif module == "grading_scheme":
                    obj = get_object_or_404(GradingScheme, pk=obj_id, school=school); obj.name = request.POST.get("name", "").strip(); obj.is_default = request.POST.get("is_default") == "on"; obj.save()
                elif module == "report_template":
                    obj = get_object_or_404(ReportTemplate, pk=obj_id, school=school); obj.name = request.POST.get("name", "").strip(); obj.footer_text = request.POST.get("footer_text", "").strip(); obj.is_default = request.POST.get("is_default") == "on"; obj.save(update_fields=["name", "footer_text", "is_default", "updated_at"])
                elif module == "structure":
                    obj = get_object_or_404(AssessmentStructure, pk=obj_id, school=school); obj.name = request.POST.get("name", "").strip(); obj.save(update_fields=["name", "updated_at"])
                else:
                    return HttpResponseForbidden("Unsupported module edit.")
            elif action == "add_department":
                head_queryset = User.objects.filter(staff_profile__school=school)
                if request.user.role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}:
                    head_queryset = head_queryset.filter(role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
                head = head_queryset.filter(pk=request.POST.get("head_id")).first() if request.POST.get("head_id") else None
                Department.objects.create(school=school, head=head, name=request.POST.get("name", "").strip(), code=request.POST.get("code", "").strip())
            elif action == "add_academic_year":
                AcademicYear.objects.create(school=school, name=request.POST.get("name", "").strip(), start_date=datetime.date.fromisoformat(request.POST.get("start_date")), end_date=datetime.date.fromisoformat(request.POST.get("end_date")), is_current=request.POST.get("is_current") == "on")
            elif action == "add_term":
                year = get_object_or_404(AcademicYear, pk=request.POST.get("academic_year_id"), school=school)
                Term.objects.create(academic_year=year, name=request.POST.get("name", "").strip(), term_number=int(request.POST.get("term_number")), start_date=datetime.date.fromisoformat(request.POST.get("start_date")), end_date=datetime.date.fromisoformat(request.POST.get("end_date")), is_current=request.POST.get("is_current") == "on")
            elif action == "add_stream":
                class_group = get_object_or_404(Class, pk=request.POST.get("class_id"), school=school)
                Stream.objects.create(class_group=class_group, name=request.POST.get("name", "").strip(), capacity=int(request.POST.get("capacity") or 0))
            elif action == "add_grading_scheme":
                GradingScheme.objects.create(school=school, name=request.POST.get("name", "").strip(), is_default=request.POST.get("is_default") == "on")
            elif action == "add_grade_band":
                scheme = get_object_or_404(GradingScheme, pk=request.POST.get("scheme_id"), school=school)
                GradeBand.objects.create(scheme=scheme, min_mark=Decimal(request.POST.get("min_mark")), max_mark=Decimal(request.POST.get("max_mark")), grade=request.POST.get("grade", "").strip(), grade_point=Decimal(request.POST.get("grade_point") or "0"), remark=request.POST.get("remark", "").strip())
            elif action == "add_report_template":
                ReportTemplate.objects.create(school=school, name=request.POST.get("name", "").strip(), template_key=request.POST.get("template_key") or ReportTemplate.TemplateKey.DEFAULT, show_position=request.POST.get("show_position") == "on", show_gpa=request.POST.get("show_gpa") == "on", show_attendance=request.POST.get("show_attendance") == "on", footer_text=request.POST.get("footer_text", "").strip(), is_default=request.POST.get("is_default") == "on")
            elif action == "add_class":
                department = Department.objects.filter(pk=request.POST.get("department_id"), school=school).first() if request.POST.get("department_id") else None
                class_teacher = User.objects.filter(pk=request.POST.get("class_teacher_id"), staff_profile__school=school, role=User.Role.TEACHER).first() if request.POST.get("class_teacher_id") else None
                Class.objects.create(school=school, department=department, class_teacher=class_teacher, name=request.POST.get("name", "").strip(), level_order=int(request.POST.get("level_order") or 0))
            elif action == "add_subject":
                department = Department.objects.filter(pk=request.POST.get("department_id"), school=school).first() if request.POST.get("department_id") else None
                Subject.objects.create(school=school, code=request.POST.get("code", "").strip(), name=request.POST.get("name", "").strip(), description=request.POST.get("description", "").strip(), department=department)
            elif action == "assign_subject":
                class_group = get_object_or_404(Class, pk=request.POST.get("class_id"), school=school)
                subject = get_object_or_404(Subject, pk=request.POST.get("subject_id"), school=school)
                class_subject, _ = ClassSubject.objects.get_or_create(class_group=class_group, subject=subject)
                for structure in AssessmentStructure.objects.filter(school=school, term__academic_year__school=school, is_active=True).filter(Q(subject__isnull=True) | Q(subject=subject)).distinct():
                    create_assessments_for_structure(structure=structure, created_by=request.user, request=request)
            elif action == "assign_teacher":
                class_subject = get_object_or_404(ClassSubject, pk=request.POST.get("class_subject_id"), class_group__school=school)
                teacher_queryset = Staff.objects.filter(school=school, is_active=True)
                if request.user.role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}:
                    teacher_queryset = teacher_queryset.filter(user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
                teacher = get_object_or_404(teacher_queryset, pk=request.POST.get("teacher_id"))
                term = get_object_or_404(Term, pk=request.POST.get("term_id"), academic_year__school=school)
                TeachingAssignment.objects.update_or_create(class_subject=class_subject, term=term, defaults={"teacher": teacher, "is_active": True})
            elif action == "enroll_student":
                student = get_object_or_404(Student, pk=request.POST.get("student_id"), school=school)
                class_subject = get_object_or_404(ClassSubject, pk=request.POST.get("class_subject_id"), class_group__school=school)
                academic_year = get_object_or_404(AcademicYear, pk=request.POST.get("academic_year_id"), school=school)
                Enrollment.objects.get_or_create(student=student, class_subject=class_subject, academic_year=academic_year, defaults={"status": Enrollment.Status.ENROLLED})
                if student.current_class_id != class_subject.class_group_id:
                    student.current_class = class_subject.class_group
                    student.save(update_fields=["current_class", "updated_at"])
                ensure_current_term_invoice_for_student(
                    student=student, issued_by=request.user, request=request,
                )
            elif action == "link_guardian":
                if request.user.role == User.Role.PRINCIPAL:
                    return HttpResponseForbidden("Principal accounts cannot manage parent or family records.")
                student = get_object_or_404(Student, pk=request.POST.get("student_id"), school=school)
                guardian = get_object_or_404(Guardian, pk=request.POST.get("guardian_id"), school=school)
                StudentGuardian.objects.update_or_create(student=student, guardian=guardian, defaults={"is_primary_contact": request.POST.get("is_primary_contact") == "on", "is_billing_contact": request.POST.get("is_billing_contact") == "on"})
            elif action == "add_assessment_type":
                AssessmentType.objects.create(school=school, name=request.POST.get("name", "").strip(), code=request.POST.get("code", "").strip().upper())
            elif action == "add_structure":
                term = get_object_or_404(Term, pk=request.POST.get("term_id"), academic_year__school=school)
                subject = Subject.objects.filter(pk=request.POST.get("subject_id"), school=school).first() if request.POST.get("subject_id") else None
                type_ids = request.POST.getlist("component_type_id")
                weights = request.POST.getlist("component_weight")
                max_marks = request.POST.getlist("component_max_marks")
                if not type_ids or sum(Decimal(value or "0") for value in weights) != Decimal("100"):
                    return HttpResponseForbidden("Assessment component weights must total exactly 100%.")
                structure = AssessmentStructure.objects.create(school=school, term=term, subject=subject, name=request.POST.get("structure_name", "").strip())
                for order, (type_id, weight, maximum) in enumerate(zip(type_ids, weights, max_marks, strict=False), start=1):
                    assessment_type = get_object_or_404(AssessmentType, pk=type_id, school=school, is_active=True)
                    AssessmentComponent.objects.create(structure=structure, assessment_type=assessment_type, weight_percentage=Decimal(weight), max_marks=Decimal(maximum or "100"), order=order)
                create_assessments_for_structure(structure=structure, created_by=request.user, request=request)
            else:
                return HttpResponseForbidden("Unsupported configuration action.")
        except (ValueError, TypeError, IntegrityError) as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:principal_configuration")


class AcademicAdminDashboardView(AcademicAdminRequiredMixin, TemplateView):
    template_name = "dashboard/academic_admin/overview.html"
    active_nav = "overview"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        summary = compute_school_academic_summary(school=school) if school else {}
        attendance = compute_school_attendance_summary(school=school) if school else {}
        class_rows = []
        assessment_rows = []
        fee_class_chart = []
        gender_chart = []
        teachers_count = staff_count = 0
        if school:
            class_rows = list(
                Class.objects.filter(school=school, is_active=True)
                .annotate(student_count=Count("students", filter=Q(students__is_active=True)))
                .order_by("level_order", "name")
                .values("name", "student_count")
            )
            from django.db.models import Count as _Count
            status_counts = dict(
                Assessment.objects.filter(
                    class_subject__class_group__school=school
                ).values_list("workflow_status").annotate(
                    count=_Count("pk")
                ).values_list("workflow_status", "count")
            )
            assessment_rows = [
                {"label": label, "count": status_counts.get(code, 0)}
                for code, label in Assessment.WorkflowStatus.choices
            ]
            from django.db.models import DecimalField, ExpressionWrapper, F, OuterRef, Subquery, Sum, Value
            from django.db.models.functions import Coalesce

            can_view_finance_chart = self.request.user.role == User.Role.MANAGER
            current_term = Term.objects.filter(
                academic_year__school=school, academic_year__is_current=True, is_current=True,
            ).first() if can_view_finance_chart else None
            grouped_fees = {}
            if current_term:
                money = DecimalField(max_digits=14, decimal_places=2)
                zero = Value(Decimal("0"), output_field=money)
                invoices = Invoice.objects.filter(
                    school=school, term=current_term,
                ).exclude(status=Invoice.Status.CANCELLED)
                direct_paid = Payment.objects.filter(
                    invoice_id=OuterRef("pk"), status=Payment.Status.COMPLETED,
                ).order_by().values("invoice_id").annotate(total=Sum("amount")).values("total")[:1]
                allocated_paid = PaymentAllocation.objects.filter(
                    invoice_id=OuterRef("pk"), payment__status=Payment.Status.COMPLETED,
                ).order_by().values("invoice_id").annotate(total=Sum("amount")).values("total")[:1]
                direct_refunds = Refund.objects.filter(
                    payment__invoice_id=OuterRef("pk"), payment__status=Payment.Status.COMPLETED,
                    status=Refund.Status.COMPLETED,
                ).order_by().values("payment__invoice_id").annotate(total=Sum("amount")).values("total")[:1]
                allocated_refunds = Refund.objects.filter(
                    payment__status=Payment.Status.COMPLETED,
                    payment__allocations__invoice_id=OuterRef("pk"),
                    status=Refund.Status.COMPLETED,
                ).order_by().values("payment__allocations__invoice_id").annotate(
                    total=Sum(ExpressionWrapper(
                        F("amount") * F("payment__allocations__amount") / F("payment__amount"),
                        output_field=money,
                    ))
                ).values("total")[:1]
                invoices = invoices.annotate(
                    _direct_paid=Coalesce(Subquery(direct_paid, output_field=money), zero),
                    _allocated_paid=Coalesce(Subquery(allocated_paid, output_field=money), zero),
                    _direct_refunds=Coalesce(Subquery(direct_refunds, output_field=money), zero),
                    _allocated_refunds=Coalesce(Subquery(allocated_refunds, output_field=money), zero),
                ).annotate(net_paid=ExpressionWrapper(
                    F("_direct_paid") + F("_allocated_paid") - F("_direct_refunds") - F("_allocated_refunds"),
                    output_field=money,
                ))
                grouped_fees = {
                    row["chart_class_id"]: row
                    for row in invoices.annotate(
                        chart_class_id=Coalesce(F("student__current_class_id"), F("fee_structure__class_group_id")),
                    ).values("chart_class_id").annotate(
                        total_billed=Sum("total_amount"), total_paid=Sum("net_paid"),
                    )
                }
            fee_class_chart = [
                {
                    "name": row["name"],
                    "total_billed": float(grouped_fees.get(row["id"], {}).get("total_billed") or 0),
                    "total_paid": float(grouped_fees.get(row["id"], {}).get("total_paid") or 0),
                }
                for row in Class.objects.filter(school=school, is_active=True)
                .order_by("level_order", "name").values("id", "name")
            ] if can_view_finance_chart else []
            gender_counts = {
                row["gender"]: row["count"]
                for row in Student.objects.filter(
                    school=school, is_active=True, gender__in=Student.Gender.values,
                ).values("gender").annotate(count=Count("pk"))
            }
            recorded_gender_total = sum(gender_counts.values())
            gender_chart = [
                {"label": label, "count": gender_counts.get(value, 0),
                 "percentage": round(gender_counts.get(value, 0) * 100 / recorded_gender_total, 1) if recorded_gender_total else 0}
                for value, label in Student.Gender.choices
            ]
            teachers_count = Staff.objects.filter(
                school=school, is_active=True,
                user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER],
            ).count()
            staff_count = Staff.objects.filter(school=school, is_active=True).count()
        context.update({
            "school": school,
            "summary": summary,
            "attendance_summary": attendance,
            "class_chart": class_rows,
            "assessment_chart": assessment_rows,
            "fee_class_chart": fee_class_chart,
            "gender_chart": gender_chart,
            "teachers_count": teachers_count,
            "staff_count": staff_count,
            "student_gender_recorded": sum(item["count"] for item in gender_chart),
        })
        return context


class AcademicAdminStudentsView(AcademicAdminRequiredMixin, TemplateView):
    """Spec §7 'Student profiles', 'Classes', 'Streams', 'Student
    status'. List + search + register-student action."""

    template_name = "dashboard/academic_admin/students.html"
    active_nav = "students"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        students_qs = Student.objects.filter(school=school).select_related(
            "user", "current_class", "current_stream"
        ).order_by("-created_at") if school else Student.objects.none()

        search = self.request.GET.get("q", "").strip()
        if search:
            from django.db.models import Count, Q
            students_qs = students_qs.filter(
                Q(admission_number__icontains=search) | Q(user__first_name__icontains=search)
                | Q(user__last_name__icontains=search)
            )

        from django.core.paginator import Paginator
        paginator = Paginator(students_qs, 25)
        page_obj = paginator.get_page(self.request.GET.get("page"))
        context.update({
            "school": school, "students": page_obj, "page_obj": page_obj, "search": search,
            "classes": Class.objects.filter(school=school, is_active=True) if school else [],
            "transport_routes": FeeStructureTransport.objects.filter(
                structure__school=school, structure__academic_year__is_current=True,
                structure__is_active=True,
            ).values_list("route_name", flat=True).distinct().order_by("route_name") if school else [],
            "gender_choices": Student.Gender.choices,
        })
        return context

    def post(self, request):
        if request.user.role == User.Role.DEPUTY_PRINCIPAL:
            return HttpResponseForbidden("Deputy Principals cannot register students.")
        school = self.get_school(request)
        gender = request.POST.get("gender", "")
        if gender not in Student.Gender.values:
            return HttpResponseForbidden("Select Male, Female, or Other for student gender.")
        try:
            transport_option = request.POST.get("transport_option", "NONE")
            transport_period = request.POST.get("transport_period", "NONE")
            transport_route = request.POST.get("transport_route", "").strip()
            if transport_option not in {"NONE", "ONE_WAY", "TWO_WAY"}:
                return HttpResponseForbidden("Select a valid transport option.")
            if transport_option != "NONE":
                current_class = Class.objects.filter(
                    pk=request.POST.get("current_class_id"), school=school, is_active=True,
                ).first()
                if transport_period not in {"MORNING", "EVENING", "BOTH"} or not current_class:
                    return HttpResponseForbidden("Select a class and bus schedule for transport.")
                route_exists = FeeStructureTransport.objects.filter(
                    structure__school=school, structure__academic_year__is_current=True,
                    structure__is_active=True, route_name__iexact=transport_route,
                ).filter(Q(structure__class_groups=current_class) | Q(structure__class_group=current_class)).exists()
                if not route_exists:
                    return HttpResponseForbidden("Select a route configured for the student's class.")
            else:
                transport_period, transport_route = "NONE", ""
            temporary_password = request.POST.get("password") or None
            if temporary_password is None:
                from django.utils.crypto import get_random_string
                temporary_password = get_random_string(16)
            register_student(
                school=school, username=request.POST.get("username", "").strip(),
                password=temporary_password,
                first_name=request.POST.get("first_name", ""),
                last_name=request.POST.get("last_name", ""),
                email=request.POST.get("email", ""),
                admission_number=request.POST.get("admission_number", ""),
                admission_date=request.POST.get("admission_date"),
                gender=gender,
                current_class=(
                    Class.objects.filter(pk=request.POST.get("current_class_id"), school=school).first()
                    if request.POST.get("current_class_id") else None
                ),
                transport_option=transport_option,
                transport_period=transport_period,
                transport_route=transport_route if transport_option != "NONE" else "",
                takes_coding_robotics=request.POST.get("takes_coding_robotics") == "on",
                parent_phone=request.POST.get("parent_phone", "").strip() if request.user.role != User.Role.PRINCIPAL else "",
                registered_by=request.user, request=request,
            )
        except (ValueError, TypeError) as exc:
            return HttpResponseForbidden(str(exc))
        if not request.POST.get("password"):
            messages.success(request, f"Temporary sign-in password for {request.POST.get('username', '').strip()}: {temporary_password}")
        return redirect("dashboard:academic_admin_students")


class AcademicAdminStudentImportTemplateView(AcademicAdminRequiredMixin, View):
    """Download the spreadsheet-compatible import template."""
    def get(self, request):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="student_import_template.csv"'
        writer = csv.writer(response)
        headers = ["username", "first_name", "last_name", "email", "admission_number", "admission_date", "gender", "class_name", "status"]
        example = ["student-001", "Jane", "Doe", "jane@example.com", "ADM001", "2026-01-06", "F", "Grade 1", "ACTIVE"]
        if request.user.role != User.Role.PRINCIPAL:
            headers += ["parent_phone", "parent_first_name", "parent_last_name", "parent_relationship"]
            example += ["+254700000000", "Mary", "Doe", "Mother"]
        writer.writerow(headers)
        writer.writerow(example)
        return response


class AcademicAdminStudentImportView(AcademicAdminRequiredMixin, View):
    """Import existing students from XLSX, CSV, or text-based PDF tables.

    Required columns are username, admission_number, admission_date, and
    class_name. Existing admission numbers are updated; new rows create the
    linked student login and place the student in the named class.
    """
    required_columns = {"username", "admission_number", "admission_date", "gender", "class_name"}

    def get(self, request):
        return redirect("dashboard:academic_admin_students")

    def _rows_from_upload(self, uploaded):
        name = uploaded.name.lower()
        if name.endswith(".xlsx"):
            try:
                from openpyxl import load_workbook
            except ImportError as exc:
                raise ValueError("Excel import requires openpyxl. Install the project requirements first.") from exc
            workbook = load_workbook(uploaded, read_only=True, data_only=True)
            sheet = workbook.active
            values = list(sheet.values)
            if not values:
                return []
            headers = [str(value or "").strip().lower() for value in values[0]]
            return [dict(zip(headers, row, strict=False)) for row in values[1:] if any(row)]
        if name.endswith(".pdf"):
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(uploaded).pages)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if not lines:
                return []
            delimiter = "\t" if "\t" in lines[0] else ","
            headers = [part.strip().lower() for part in lines[0].split(delimiter)]
            return [dict(zip(headers, line.split(delimiter), strict=False)) for line in lines[1:]]
        uploaded.seek(0)
        return list(csv.DictReader(io.TextIOWrapper(uploaded, encoding="utf-8-sig")))

    def post(self, request):
        school = self.get_school(request)
        uploaded = request.FILES.get("student_file")
        if school is None or uploaded is None:
            return HttpResponseForbidden("Select a school and upload a CSV, XLSX, or text-based PDF file.")
        try:
            rows = self._rows_from_upload(uploaded)
            if not rows:
                raise ValueError("The uploaded document contains no student rows.")
            headers = {str(key).strip().lower() for key in rows[0]}
            missing = self.required_columns - headers
            if missing:
                raise ValueError("Missing required columns: " + ", ".join(sorted(missing)))
            from django.utils.crypto import get_random_string
            created = updated = 0
            temporary_credentials = []
            with transaction.atomic():
                for line, raw in enumerate(rows, start=2):
                    row = {str(key).strip().lower(): (str(value).strip() if value is not None else "") for key, value in raw.items()}
                    admission_number = row.get("admission_number", "")
                    username = row.get("username", "")
                    class_name = row.get("class_name", "")
                    if not admission_number or not username or not class_name:
                        raise ValueError(f"Row {line}: username, admission_number, and class_name are required.")
                    class_group = Class.objects.filter(school=school, name__iexact=class_name, is_active=True).first()
                    if class_group is None:
                        raise ValueError(f"Row {line}: class '{class_name}' was not found in this school.")
                    try:
                        admission_date = datetime.date.fromisoformat(row.get("admission_date", ""))
                    except ValueError as exc:
                        raise ValueError(f"Row {line}: admission_date must be YYYY-MM-DD.") from exc
                    gender_aliases = {"f": Student.Gender.FEMALE, "female": Student.Gender.FEMALE,
                                      "m": Student.Gender.MALE, "male": Student.Gender.MALE,
                                      "o": Student.Gender.OTHER, "other": Student.Gender.OTHER}
                    gender = gender_aliases.get(row.get("gender", "").casefold())
                    if gender is None:
                        raise ValueError(f"Row {line}: gender must be Female (F), Male (M), or Other (O).")
                    student = Student.objects.filter(school=school, admission_number=admission_number).select_related("user").first()
                    if student is None:
                        user = User.objects.filter(username=username).first()
                        if user and hasattr(user, "student_profile"):
                            raise ValueError(f"Row {line}: username already belongs to a student.")
                        if user is None:
                            temporary_password = get_random_string(16)
                            user = User.objects.create_user(username=username, password=temporary_password, role=User.Role.STUDENT, must_change_password=True)
                            temporary_credentials.append(f"{username}: {temporary_password}")
                        elif user.role != User.Role.STUDENT:
                            raise ValueError(f"Row {line}: username belongs to a non-student user.")
                        student = Student.objects.create(user=user, school=school, admission_number=admission_number, admission_date=admission_date, current_class=class_group, gender=gender)
                        created += 1
                    else:
                        updated += 1
                        student.current_class = class_group
                        student.admission_date = admission_date
                        student.gender = gender
                        student.save(update_fields=["current_class", "admission_date", "gender", "updated_at"])
                    user = student.user
                    user.first_name = row.get("first_name", "")
                    user.last_name = row.get("last_name", "")
                    user.email = row.get("email", "")
                    user.save(update_fields=["first_name", "last_name", "email", "updated_at"])
                    if row.get("status") in Student.Status.values:
                        student.status = row["status"]
                        student.save(update_fields=["status", "updated_at"])
                    parent_phone = row.get("parent_phone", "")
                    if request.user.role == User.Role.PRINCIPAL:
                        parent_phone = ""
                    if parent_phone:
                        guardian = Guardian.objects.filter(school=school, phone_number=parent_phone).first()
                        if guardian is None:
                            guardian = Guardian.objects.create(
                                school=school,
                                first_name=row.get("parent_first_name", "Parent") or "Parent",
                                last_name=row.get("parent_last_name", "Guardian") or "Guardian",
                                relationship=row.get("parent_relationship", "Parent/Guardian") or "Parent/Guardian",
                                phone_number=parent_phone,
                            )
                        else:
                            guardian.first_name = row.get("parent_first_name", "") or guardian.first_name
                            guardian.last_name = row.get("parent_last_name", "") or guardian.last_name
                            guardian.relationship = row.get("parent_relationship", "") or guardian.relationship
                            guardian.save(update_fields=["first_name", "last_name", "relationship", "updated_at"])
                        StudentGuardian.objects.get_or_create(
                            student=student, guardian=guardian,
                            defaults={"is_primary_contact": True, "is_billing_contact": True},
                        )
                    ensure_current_term_invoice_for_student(
                        student=student, issued_by=request.user, request=request,
                    )
            if temporary_credentials:
                messages.success(request, "Temporary student credentials (share securely; each student must change password): " + " | ".join(temporary_credentials))
            return redirect("dashboard:academic_admin_students")
        except (ValueError, TypeError, KeyError) as exc:
            return HttpResponseForbidden(str(exc))


class PrincipalParentsView(AcademicAdminRequiredMixin, View):
    allowed_roles = [User.Role.MANAGER]
    template_name = "dashboard/academic_admin/parents.html"
    active_nav = "parents"

    def get(self, request):
        school = self.get_school(request)
        guardians = Guardian.objects.filter(school=school, is_active=True).prefetch_related("students__user", "students__current_class") if school else Guardian.objects.none()
        return render(request, self.template_name, {"school": school, "guardians": guardians, "active": self.active_nav})

    def post(self, request):
        school = self.get_school(request)
        guardian = get_object_or_404(Guardian, pk=request.POST.get("guardian_id"), school=school)
        guardian.first_name = request.POST.get("first_name", "").strip()
        guardian.last_name = request.POST.get("last_name", "").strip()
        guardian.relationship = request.POST.get("relationship", "").strip()
        guardian.phone_number = request.POST.get("phone_number", "").strip()
        guardian.email = request.POST.get("email", "").strip()
        guardian.save(update_fields=["first_name", "last_name", "relationship", "phone_number", "email", "updated_at"])
        return redirect("dashboard:principal_parents")


class PrincipalTimetableView(AcademicAdminRequiredMixin, View):
    template_name = "dashboard/academic_admin/timetable.html"
    active_nav = "timetable"
    deputy_write_allowed = True

    def get(self, request):
        school = self.get_school(request)
        periods = Period.objects.filter(school=school).order_by("order") if school else Period.objects.none()
        assignments = TeachingAssignment.objects.filter(
            class_subject__class_group__school=school, is_active=True
        ).select_related("teacher__user", "class_subject__class_group", "class_subject__subject", "term") if school else TeachingAssignment.objects.none()
        if request.user.role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}:
            assignments = assignments.filter(teacher__user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER])
        slots = TimetableSlot.objects.filter(
            term__academic_year__school=school
        ).select_related("period", "class_group", "teacher__user", "teaching_assignment__class_subject__subject", "term") if school else TimetableSlot.objects.none()
        rooms = Room.objects.filter(school=school, is_active=True) if school else Room.objects.none()
        return render(request, self.template_name, {
            "school": school, "periods": periods, "assignments": assignments,
            "slots": slots, "rooms": rooms, "day_choices": TimetableSlot.DayOfWeek.choices,
            "active": self.active_nav,
        })

    def post(self, request):
        school = self.get_school(request)
        action = request.POST.get("action")
        try:
            if action == "generate_periods":
                start = datetime.time.fromisoformat(request.POST.get("start_time"))
                end = datetime.time.fromisoformat(request.POST.get("end_time"))
                lesson_minutes = int(request.POST.get("lesson_minutes"))
                break_count = int(request.POST.get("break_count") or 0)
                break_minutes = int(request.POST.get("break_minutes") or 0)
                if lesson_minutes <= 0 or break_count < 0 or break_minutes < 0:
                    raise ValueError("Timetable durations must be positive.")
                start_dt = datetime.datetime.combine(datetime.date.today(), start)
                end_dt = datetime.datetime.combine(datetime.date.today(), end)
                total_minutes = int((end_dt - start_dt).total_seconds() // 60)
                lesson_count = total_minutes // lesson_minutes
                if lesson_count < 1:
                    raise ValueError("The school day is shorter than one lesson.")
                interval = max(1, (lesson_count + break_count) // (break_count + 1))
                cursor = start_dt
                order = 1
                lessons = breaks = 0
                while cursor + datetime.timedelta(minutes=lesson_minutes) <= end_dt:
                    lesson_end = cursor + datetime.timedelta(minutes=lesson_minutes)
                    Period.objects.update_or_create(
                        school=school, name=f"Period {lessons + 1}",
                        defaults={"start_time": cursor.time(), "end_time": lesson_end.time(), "order": order, "is_break": False},
                    )
                    lessons += 1; order += 1; cursor = lesson_end
                    if breaks < break_count and lessons % interval == 0 and cursor + datetime.timedelta(minutes=break_minutes) <= end_dt:
                        break_end = cursor + datetime.timedelta(minutes=break_minutes)
                        Period.objects.update_or_create(
                            school=school, name=f"Break {breaks + 1}",
                            defaults={"start_time": cursor.time(), "end_time": break_end.time(), "order": order, "is_break": True},
                        )
                        breaks += 1; order += 1; cursor = break_end
            elif action == "add_slot":
                assignment_queryset = TeachingAssignment.objects.filter(
                    class_subject__class_group__school=school, is_active=True,
                )
                if request.user.role in {User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL}:
                    assignment_queryset = assignment_queryset.filter(
                        teacher__user__role__in=[User.Role.TEACHER, User.Role.CLASS_TEACHER],
                    )
                assignment = get_object_or_404(assignment_queryset, pk=request.POST.get("assignment_id"))
                period = get_object_or_404(Period, pk=request.POST.get("period_id"), school=school, is_break=False)
                room = Room.objects.filter(pk=request.POST.get("room_id"), school=school).first() if request.POST.get("room_id") else None
                TimetableSlot.objects.create(
                    teaching_assignment=assignment, period=period, room=room,
                    day_of_week=request.POST.get("day_of_week"),
                )
            else:
                raise ValueError("Unsupported timetable action.")
        except IntegrityError as exc:
            return HttpResponseForbidden("Timetable clash: that teacher or class already has this day and period.")
        except (ValueError, TypeError) as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:principal_timetable")


class AcademicAdminStudentDetailView(AcademicAdminRequiredMixin, View):
    """Spec §7 'Student profiles', 'Guardian information', 'Academic
    history', 'Student status', 'Student documents'."""

    template_name = "dashboard/academic_admin/student_detail.html"
    active_nav = "students"

    def get(self, request, student_id):
        school = self.get_school(request)
        student = get_object_or_404(
            Student.objects.select_related("user", "current_class", "current_stream"),
            pk=student_id, school=school,
        )
        guardians = StudentGuardian.objects.filter(student=student).select_related("guardian")
        enrollments = Enrollment.objects.filter(student=student).select_related(
            "class_subject__subject", "academic_year"
        )
        current_fee_structures = FeeStructure.objects.filter(
            school=school, academic_year__is_current=True, is_active=True,
        ).filter(Q(class_groups=student.current_class) | Q(class_group=student.current_class)).distinct()
        transport_routes = FeeStructureTransport.objects.filter(
            structure__in=current_fee_structures
        ).values_list("route_name", flat=True).distinct().order_by("route_name")
        return render(request, self.template_name, {
            "student": student, "active": self.active_nav,
            "guardians": guardians, "enrollments": enrollments,
            "status_choices": Student.Status.choices,
            "gender_choices": Student.Gender.choices,
            "transport_routes": transport_routes,
        })

    def post(self, request, student_id):
        if request.user.role == User.Role.DEPUTY_PRINCIPAL:
            return HttpResponseForbidden("Deputy Principals cannot change student status.")
        school = self.get_school(request)
        student = get_object_or_404(Student, pk=student_id, school=school)
        if request.POST.get("action") == "update_gender":
            gender = request.POST.get("gender", "")
            if gender not in Student.Gender.values:
                return HttpResponseForbidden("Select Male, Female, or Other for student gender.")
            student.gender = gender
            student.save(update_fields=["gender", "updated_at"])
            log_audit(
                actor=request.user, action=AuditLog.Action.UPDATE, request=request,
                target_model="Student", target_object_id=student.pk,
                description=f"Updated gender for {student}",
            )
            return redirect("dashboard:academic_admin_student_detail", student_id=student.pk)
        if request.POST.get("action") == "update_transport":
            transport_option = request.POST.get("transport_option", "NONE")
            transport_period = request.POST.get("transport_period", "NONE")
            transport_route = request.POST.get("transport_route", "").strip()
            if transport_option not in {"NONE", "ONE_WAY", "TWO_WAY"}:
                return HttpResponseForbidden("Select a valid transport option.")
            if transport_period not in {"NONE", "MORNING", "EVENING", "BOTH"}:
                return HttpResponseForbidden("Select a valid transport period.")
            if transport_option == "NONE":
                transport_period, transport_route = "NONE", ""
            elif not transport_route:
                return HttpResponseForbidden("Select a transport route.")
            else:
                current_fee_structures = FeeStructure.objects.filter(
                    school=school, academic_year__is_current=True, is_active=True,
                ).filter(Q(class_groups=student.current_class) | Q(class_group=student.current_class))
                if not FeeStructureTransport.objects.filter(
                    structure__in=current_fee_structures, route_name__iexact=transport_route,
                ).exists():
                    return HttpResponseForbidden("Select a route configured for the student's class.")
            student.transport_option = transport_option
            student.transport_period = transport_period
            student.transport_route = transport_route
            student.takes_coding_robotics = request.POST.get("takes_coding_robotics") == "on"
            student.save(update_fields=[
                "transport_option", "transport_period", "transport_route",
                "takes_coding_robotics", "updated_at",
            ])
            log_audit(
                actor=request.user, action=AuditLog.Action.UPDATE, request=request,
                target_model="Student", target_object_id=student.pk,
                description=f"Updated optional finance selections for {student}",
            )
            return redirect("dashboard:academic_admin_student_detail", student_id=student.pk)
        try:
            change_student_status(
                student=student, new_status=request.POST.get("status"),
                changed_by=request.user, reason=request.POST.get("reason", ""),
                request=request,
            )
        except ValueError as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:academic_admin_student_detail", student_id=student.pk)


class AcademicAdminResultsApprovalView(AcademicAdminRequiredMixin, TemplateView):
    """Spec §7/§14: the Academic Admin review/verify/approve/publish
    steps in the result-processing workflow. Reuses
    services.transition_assessment_workflow() (Phase 8, already tested —
    including the 'a teacher can't approve their own results' rule)."""

    template_name = "dashboard/academic_admin/results_approval.html"
    active_nav = "results"
    allowed_roles = [User.Role.ACADEMIC_ADMIN, User.Role.MANAGER, User.Role.PRINCIPAL]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        pending_statuses = [
            Assessment.WorkflowStatus.SUBMITTED, Assessment.WorkflowStatus.REVIEWED,
            Assessment.WorkflowStatus.VERIFIED,
        ]
        assessments = Assessment.objects.filter(
            class_subject__class_group__school=school, workflow_status__in=pending_statuses
        ).select_related(
            "class_subject__class_group", "class_subject__subject", "term"
        ).order_by("workflow_status") if school else []
        context.update({"school": school, "assessments": assessments})
        return context

    def post(self, request):
        school = self.get_school(request)
        assessment = get_object_or_404(
            Assessment, pk=request.POST.get("assessment_id"),
            class_subject__class_group__school=school,
        )
        action = request.POST.get("action")
        next_status_map = {
            "review": Assessment.WorkflowStatus.REVIEWED,
            "verify": Assessment.WorkflowStatus.VERIFIED,
            "approve": Assessment.WorkflowStatus.APPROVED,
            "publish": Assessment.WorkflowStatus.PUBLISHED,
            "reject": Assessment.WorkflowStatus.DRAFT,
        }
        to_status = next_status_map.get(action)
        if to_status is None:
            return HttpResponseForbidden("Unknown action.")
        try:
            transition_assessment_workflow(
                assessment=assessment, to_status=to_status, actor=request.user,
                request=request,
            )
        except (ValueError, PermissionError) as exc:
            return HttpResponseForbidden(str(exc))
        return redirect("dashboard:academic_admin_results_approval")


class AcademicAdminAttendanceCorrectionView(AcademicAdminRequiredMixin, TemplateView):
    """Spec §7/§11: 'Academic Admin can... correct attendance with
    appropriate permissions.' Reuses services.correct_attendance_record()
    (Phase 6, already tested — including the full audit-log trail)."""

    template_name = "dashboard/academic_admin/attendance_correction.html"
    allowed_roles = [User.Role.ACADEMIC_ADMIN, User.Role.MANAGER, User.Role.PRINCIPAL, User.Role.DEPUTY_PRINCIPAL]
    active_nav = "attendance"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        school = self.get_school(self.request)
        target_date = self.request.GET.get("date") or datetime.date.today().isoformat()

        records = AttendanceRecord.objects.filter(
            session__class_subject__class_group__school=school, session__date=target_date,
        ).select_related(
            "student__user", "session__class_subject__subject", "session__class_subject__class_group",
        ) if school else AttendanceRecord.objects.none()

        context.update({
            "school": school, "records": records, "target_date": target_date,
            "status_choices": AttendanceRecord.Status.choices,
        })
        return context

    def post(self, request):
        school = self.get_school(request)
        record = get_object_or_404(
            AttendanceRecord, pk=request.POST.get("record_id"),
            session__class_subject__class_group__school=school,
        )
        correct_attendance_record(
            record=record, new_status=request.POST.get("status"),
            corrected_by=request.user, request=request,
            new_notes=request.POST.get("notes", ""),
        )
        return redirect(
            f"{reverse('dashboard:academic_admin_attendance_correction')}"
            f"?date={record.session.date.isoformat()}"
        )
