"""
Absolute path: SMS/smsApp/models.py

Phase 1B — Custom User model + RBAC foundation.

Design notes:
- `role` is a coarse label used for dashboard routing / UI branching
  (e.g. "show Finance Admin sidebar").
- Actual authorization decisions (§4 of spec) must use Django's
  built-in Group + Permission framework, NOT `role` alone — this
  satisfies "use permissions in addition to roles, do not rely
  solely on hard-coded role checks."
- Fine-grained permissions (students.view, results.approve, etc.)
  are declared via `Meta.permissions` on the relevant domain models
  as those apps are built (Phase 3+), then attached to Groups
  matching each Role via the admin or a data migration.
"""
from decimal import Decimal
import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models

from .validators import (
    validate_course_material_content,
    validate_document_content,
    validate_file_size,
    validate_image_content,
    validate_pdf_content,
)



class User(AbstractUser):
    class Role(models.TextChoices):
        SUPER_ADMIN = "SUPER_ADMIN", "Super Admin"
        STAFF_ADMIN = "STAFF_ADMIN", "Staff Admin"
        ACADEMIC_ADMIN = "ACADEMIC_ADMIN", "Academic Admin"
        FINANCE_ADMIN = "FINANCE_ADMIN", "Finance Admin"
        TEACHER = "TEACHER", "Teacher/Lecturer"
        EXAM_OFFICER = "EXAM_OFFICER", "Examination Officer"
        CLASS_TEACHER = "CLASS_TEACHER", "Class Teacher"
        DEPARTMENT_HEAD = "DEPARTMENT_HEAD", "Department Head"
        ACCOUNTANT = "ACCOUNTANT", "Accountant/Finance Officer"
        LIBRARIAN = "LIBRARIAN", "Librarian"
        STUDENT = "STUDENT", "Student"
        PARENT = "PARENT", "Parent/Guardian"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.STUDENT,
        db_index=True,
        help_text="Coarse role for UI/dashboard routing. Authorization "
                   "decisions must still check Django permissions.",
    )
    phone_number = models.CharField(max_length=20, blank=True)
    is_locked = models.BooleanField(
        default=False,
        help_text="Account lock distinct from is_active — used for "
                   "Super Admin 'lock/unlock account' action (§5).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users"
        verbose_name = "User"
        verbose_name_plural = "Users"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_full_name() or self.username} ({self.get_role_display()})"

    def save(self, *args, **kwargs):
        # `createsuperuser` (and any code setting is_superuser=True) has no
        # concept of our custom `role` field, so it silently keeps the
        # model default (STUDENT). Self-correct here so is_superuser and
        # role never disagree — otherwise role-based routing/permission
        # checks (DashboardRouterView, RoleRequiredMixin) misclassify
        # superusers as students.
        if self.is_superuser:
            self.role = self.Role.SUPER_ADMIN
        super().save(*args, **kwargs)


# =============================================================================
# Phase 2 — Academic structure
# Hierarchy (spec §7): AcademicYear → Term → Department → Program → Class
#                      → Subject/Course → Teacher
# School/Campus/Stream are siblings supporting that hierarchy (spec §24).
# Multi-school scalability (spec §3 "Scalability") is built in now via the
# School FK on every node, even though the first deployment serves one school.
# =============================================================================

class School(models.Model):
    """Root tenant. Every other academic-structure model traces back here
    so the schema is multi-school-ready without a later migration."""

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20, unique=True)
    motto = models.CharField(max_length=255, blank=True)
    logo = models.ImageField(
        upload_to="school/logos/", blank=True, null=True,
        validators=[validate_file_size(5), validate_image_content],
    )
    address = models.TextField(blank=True)
    phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    established_date = models.DateField(blank=True, null=True)
    enable_position_ranking = models.BooleanField(
        default=True,
        help_text="Spec §13: some schools choose not to rank students. "
                   "Result-processing/report-book views must check this "
                   "flag before showing or computing positions.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "schools"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Campus(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="campuses")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20)
    address = models.TextField(blank=True)
    is_main = models.BooleanField(
        default=False, help_text="Marks the school's primary/head campus."
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "campuses"
        ordering = ["school", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_campus_code_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class Department(models.Model):
    """Academic or administrative department (spec §5, §7).
    `head` intentionally points at User, not a Staff model — Staff profiles
    land in Phase 3; role is filtered so only staff-capable users qualify."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="departments")
    campus = models.ForeignKey(
        Campus, on_delete=models.SET_NULL, related_name="departments",
        blank=True, null=True,
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20)
    head = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="departments_headed",
        blank=True, null=True,
        limit_choices_to={"role__in": ["DEPARTMENT_HEAD", "TEACHER", "STAFF_ADMIN"]},
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "departments"
        ordering = ["school", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_department_code_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class Program(models.Model):
    """e.g. '8-4-4', 'CBC', 'Cambridge IGCSE', 'Bachelor of Science' —
    supports both school-style and university-style structures (spec §7)."""

    class ProgramType(models.TextChoices):
        SCHOOL = "SCHOOL", "School-style (Grade/Class based)"
        UNIVERSITY = "UNIVERSITY", "University-style (Credit/Course based)"

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="programs")
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, related_name="programs",
        blank=True, null=True,
    )
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=20)
    program_type = models.CharField(
        max_length=15, choices=ProgramType.choices, default=ProgramType.SCHOOL
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "programs"
        ordering = ["school", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_program_code_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class AcademicYear(models.Model):
    """e.g. '2026/2027'. Top of the academic hierarchy (spec §7)."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="academic_years")
    name = models.CharField(max_length=20, help_text="e.g. '2026/2027'")
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "academic_years"
        ordering = ["-start_date"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name"], name="uniq_academic_year_per_school"
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="academic_year_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"

    def save(self, *args, **kwargs):
        # Only one current academic year per school (dashboard §5 relies on this).
        if self.is_current:
            AcademicYear.objects.filter(
                school=self.school, is_current=True
            ).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)


class Term(models.Model):
    """Term or Semester within an AcademicYear (spec §7 'Term/Semester')."""

    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="terms"
    )
    name = models.CharField(max_length=50, help_text="e.g. 'Term 1', 'Semester 1'")
    term_number = models.PositiveSmallIntegerField()
    start_date = models.DateField()
    end_date = models.DateField()
    is_current = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "terms"
        ordering = ["academic_year", "term_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["academic_year", "term_number"], name="uniq_term_number_per_year"
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__gt=models.F("start_date")),
                name="term_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} - {self.academic_year.name}"

    def save(self, *args, **kwargs):
        if self.is_current:
            Term.objects.filter(
                academic_year__school=self.academic_year.school, is_current=True
            ).exclude(pk=self.pk).update(is_current=False)
        super().save(*args, **kwargs)


class Class(models.Model):
    """e.g. 'Grade 10', 'Form 2', 'Year 1 Computer Science'."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="classes")
    campus = models.ForeignKey(
        Campus, on_delete=models.SET_NULL, related_name="classes", blank=True, null=True
    )
    program = models.ForeignKey(Program, on_delete=models.CASCADE, related_name="classes")
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, related_name="classes", blank=True, null=True
    )
    name = models.CharField(max_length=100, help_text="e.g. 'Grade 10', 'Form 2'")
    level_order = models.PositiveSmallIntegerField(
        default=0, help_text="Sort order for progression, e.g. Grade 1=1, Grade 2=2."
    )
    class_teacher = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="classes_led",
        blank=True, null=True,
        limit_choices_to={"role__in": ["CLASS_TEACHER", "TEACHER"]},
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "classes"
        ordering = ["school", "program", "level_order", "name"]
        verbose_name_plural = "Classes"
        constraints = [
            models.UniqueConstraint(
                fields=["program", "name"], name="uniq_class_name_per_program"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.program.name})"


class Stream(models.Model):
    """e.g. 'Stream A', 'Blue Stream' — subdivision of a Class."""

    class_group = models.ForeignKey(Class, on_delete=models.CASCADE, related_name="streams")
    name = models.CharField(max_length=50)
    capacity = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "streams"
        ordering = ["class_group", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_group", "name"], name="uniq_stream_name_per_class"
            )
        ]

    def __str__(self) -> str:
        return f"{self.class_group.name} - {self.name}"


# =============================================================================
# Phase 3 — People: Guardian, Student, Staff
# Spec refs: §6 (Staff management), §7 (Student management), §24 (DB design),
#            §36 (unique admission number), §39 (multi-school readiness)
# =============================================================================

class Guardian(models.Model):
    """Parent/Guardian contact record. Deliberately NOT a `User` by default —
    spec §18 implies a Parent Portal, but a guardian record must be able to
    exist before any portal login is issued (e.g. entered at admission time).
    `user` is optional and links back once a portal account is created."""

    user = models.OneToOneField(
        "smsApp.User", on_delete=models.SET_NULL, related_name="guardian_profile",
        blank=True, null=True,
        limit_choices_to={"role": "PARENT"},
    )
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="guardians")
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    relationship = models.CharField(
        max_length=50, help_text="e.g. 'Mother', 'Father', 'Uncle', 'Legal Guardian'"
    )
    phone_number = models.CharField(max_length=20)
    alternate_phone_number = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    national_id = models.CharField(max_length=50, blank=True)
    occupation = models.CharField(max_length=150, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "guardians"
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name} ({self.relationship})"


class Student(models.Model):
    """Spec §7 statuses reproduced verbatim; §36 requires a unique admission
    number (unique per school, not globally, per §39 multi-school readiness).
    `photo`/`documents` use local storage now — swapped to Supabase Storage
    in Phase 17 (spec §6/§29) without changing this model's public API."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        GRADUATED = "GRADUATED", "Graduated"
        SUSPENDED = "SUSPENDED", "Suspended"
        TRANSFERRED = "TRANSFERRED", "Transferred"
        DEFERRED = "DEFERRED", "Deferred"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"
        EXPELLED = "EXPELLED", "Expelled"
        ALUMNI = "ALUMNI", "Alumni"

    class Gender(models.TextChoices):
        MALE = "M", "Male"
        FEMALE = "F", "Female"
        OTHER = "O", "Other"

    user = models.OneToOneField(
        "smsApp.User", on_delete=models.CASCADE, related_name="student_profile",
        limit_choices_to={"role": "STUDENT"},
    )
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="students")
    admission_number = models.CharField(max_length=30)
    admission_date = models.DateField()
    date_of_birth = models.DateField(blank=True, null=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, blank=True)
    photo = models.ImageField(
        upload_to="students/photos/", blank=True, null=True,
        validators=[validate_file_size(5), validate_image_content],
    )
    national_id = models.CharField(
        max_length=50, blank=True, help_text="Birth certificate no. or national ID."
    )
    current_class = models.ForeignKey(
        Class, on_delete=models.SET_NULL, related_name="students", blank=True, null=True
    )
    current_stream = models.ForeignKey(
        Stream, on_delete=models.SET_NULL, related_name="students", blank=True, null=True
    )
    program = models.ForeignKey(
        Program, on_delete=models.SET_NULL, related_name="students", blank=True, null=True
    )
    status = models.CharField(
        max_length=15, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    guardians = models.ManyToManyField(
        Guardian, through="StudentGuardian", related_name="students"
    )
    address = models.TextField(blank=True)
    blood_group = models.CharField(max_length=5, blank=True)
    medical_notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "students"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "admission_number"],
                name="uniq_admission_number_per_school",
            )
        ]

    def __str__(self) -> str:
        return f"{self.user.get_full_name() or self.user.username} ({self.admission_number})"


class StudentGuardian(models.Model):
    """Through-table for Student<->Guardian (§7 'Guardian information').
    A student can have multiple guardians; one is flagged primary contact."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    guardian = models.ForeignKey(Guardian, on_delete=models.CASCADE)
    is_primary_contact = models.BooleanField(default=False)
    is_emergency_contact = models.BooleanField(default=True)
    is_billing_contact = models.BooleanField(
        default=False,
        help_text="Who fee invoices/statements are addressed to (Finance module, Phase 19).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "student_guardians"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "guardian"], name="uniq_student_guardian_pair"
            )
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.guardian}"


class Staff(models.Model):
    """Spec §6 Staff management fields. `employment_status` distinct from
    `User.is_active`/`is_locked` — a staff member can be ON_LEAVE while their
    login stays active. Qualifications/certifications/documents modeled as
    separate tables (below) rather than JSON/text blobs, per DRY (§3) and so
    Training/Performance (§6 'Staff performance') can reference them later."""

    class EmploymentStatus(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ON_LEAVE = "ON_LEAVE", "On Leave"
        SUSPENDED = "SUSPENDED", "Suspended"
        TERMINATED = "TERMINATED", "Terminated"
        RETIRED = "RETIRED", "Retired"
        RESIGNED = "RESIGNED", "Resigned"

    class EmploymentType(models.TextChoices):
        FULL_TIME = "FULL_TIME", "Full-Time"
        PART_TIME = "PART_TIME", "Part-Time"
        CONTRACT = "CONTRACT", "Contract"
        INTERN = "INTERN", "Intern"

    user = models.OneToOneField(
        "smsApp.User", on_delete=models.CASCADE, related_name="staff_profile",
        limit_choices_to={
            "role__in": [
                "STAFF_ADMIN", "ACADEMIC_ADMIN", "FINANCE_ADMIN", "TEACHER",
                "EXAM_OFFICER", "CLASS_TEACHER", "DEPARTMENT_HEAD",
                "ACCOUNTANT", "LIBRARIAN",
            ]
        },
    )
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="staff_members")
    staff_id = models.CharField(max_length=30)
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, related_name="staff_members",
        blank=True, null=True,
    )
    job_title = models.CharField(max_length=150)
    employment_type = models.CharField(
        max_length=15, choices=EmploymentType.choices, default=EmploymentType.FULL_TIME
    )
    employment_status = models.CharField(
        max_length=15, choices=EmploymentStatus.choices, default=EmploymentStatus.ACTIVE,
        db_index=True,
    )
    date_hired = models.DateField()
    date_left = models.DateField(blank=True, null=True)
    photo = models.ImageField(
        upload_to="staff/photos/", blank=True, null=True,
        validators=[validate_file_size(5), validate_image_content],
    )
    national_id = models.CharField(max_length=50, blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "staff"
        verbose_name_plural = "Staff"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "staff_id"], name="uniq_staff_id_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user.get_full_name() or self.user.username} ({self.staff_id})"


class StaffQualification(models.Model):
    """Spec §6 'Qualifications' / 'Certifications' as first-class rows,
    not a free-text field — supports future filtering/reporting (§32)."""

    class QualificationType(models.TextChoices):
        DEGREE = "DEGREE", "Degree"
        DIPLOMA = "DIPLOMA", "Diploma"
        CERTIFICATE = "CERTIFICATE", "Certificate"
        LICENSE = "LICENSE", "Professional License"
        OTHER = "OTHER", "Other"

    staff = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="qualifications"
    )
    qualification_type = models.CharField(
        max_length=15, choices=QualificationType.choices
    )
    title = models.CharField(max_length=255, help_text="e.g. 'B.Ed Mathematics'")
    institution = models.CharField(max_length=255, blank=True)
    year_obtained = models.PositiveSmallIntegerField(blank=True, null=True)
    document = models.FileField(
        upload_to="staff/qualifications/", blank=True, null=True,
        validators=[validate_file_size(10), validate_document_content],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "staff_qualifications"
        ordering = ["-year_obtained"]

    def __str__(self) -> str:
        return f"{self.title} - {self.staff}"


# =============================================================================
# Phase 4 — Audit logging + login history
# Spec refs: §5 (audit logs), §27 (audit logging as a security requirement),
#            §33 (audit logging), §37/§38 (never silently change data)
# =============================================================================

class AuditLog(models.Model):
    """Generic audit trail. Rows are written via `smsApp.services.log_audit()`
    (Phase 4 service layer), never edited or deleted from application code —
    enforced by omitting update/delete from ModelAdmin (see admin.py)."""

    class Action(models.TextChoices):
        CREATE = "CREATE", "Create"
        UPDATE = "UPDATE", "Update"
        DELETE = "DELETE", "Delete"
        LOGIN = "LOGIN", "Login"
        LOGOUT = "LOGOUT", "Logout"
        LOGIN_FAILED = "LOGIN_FAILED", "Failed Login"
        APPROVE = "APPROVE", "Approve"
        PUBLISH = "PUBLISH", "Publish"
        LOCK = "LOCK", "Lock Account"
        UNLOCK = "UNLOCK", "Unlock Account"
        ROLE_CHANGE = "ROLE_CHANGE", "Role Change"
        PERMISSION_CHANGE = "PERMISSION_CHANGE", "Permission Change"
        PASSWORD_RESET = "PASSWORD_RESET", "Password Reset"
        OTHER = "OTHER", "Other"

    actor = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="audit_logs",
        blank=True, null=True, help_text="Who performed the action.",
    )
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    target_model = models.CharField(
        max_length=100, blank=True, help_text="e.g. 'Student', 'User'."
    )
    target_object_id = models.CharField(max_length=50, blank=True)
    description = models.CharField(max_length=255, blank=True)
    previous_value = models.JSONField(blank=True, null=True)
    new_value = models.JSONField(blank=True, null=True)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "audit_logs"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.actor} - {self.action} - {self.target_model} @ {self.created_at:%Y-%m-%d %H:%M}"


class LoginHistory(models.Model):
    """Spec §5 'View login history'. Separate from AuditLog's LOGIN entries
    so login-history queries (frequent, per-user) don't scan the whole
    audit trail table."""

    user = models.ForeignKey(
        "smsApp.User", on_delete=models.CASCADE, related_name="login_history"
    )
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.CharField(max_length=255, blank=True)
    was_successful = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "login_history"
        ordering = ["-created_at"]
        verbose_name_plural = "Login history"

    def __str__(self) -> str:
        status = "success" if self.was_successful else "failed"
        return f"{self.user} - {status} @ {self.created_at:%Y-%m-%d %H:%M}"


# =============================================================================
# Phase 5 — Curriculum: Subject/Course, class assignment, teaching
# assignment, enrollment.
# Spec refs: §7 (Enrollment), §8 (Curriculum Management), §9 (Teacher
# Dashboard "My classes/My subjects").
#
# Design: `Subject` is the catalog definition (reusable across classes/years).
# `ClassSubject` is "assign courses to classes" (§8) — which subjects are
# taught in which class. `TeachingAssignment` is "assign teachers" (§8) —
# who teaches a given ClassSubject in a given Term. `Enrollment` is the
# student-facing record (§7) — which students are actually taking it.
# Splitting these four instead of one wide table keeps each concern testable
# independently and matches §3 "Separation of concerns".
# =============================================================================

class Subject(models.Model):
    """Catalog entry — spec §8: code, name, description, credit hours,
    prerequisites. `credit_hours` is nullable/blank because school-style
    programs (spec §7 'support both school-style and university-style')
    typically don't use credit hours at all."""

    class SubjectType(models.TextChoices):
        CORE = "CORE", "Core"
        ELECTIVE = "ELECTIVE", "Elective"

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="subjects")
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, related_name="subjects",
        blank=True, null=True,
    )
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    subject_type = models.CharField(
        max_length=10, choices=SubjectType.choices, default=SubjectType.CORE
    )
    credit_hours = models.DecimalField(
        max_digits=4, decimal_places=1, blank=True, null=True,
        help_text="University-style credit hours. Leave blank for school-style subjects.",
    )
    prerequisites = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="unlocks"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "subjects"
        ordering = ["school", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_subject_code_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


class ClassSubject(models.Model):
    """'Assign courses to classes' (spec §8). A Subject taught within a
    specific Class — the unit that TeachingAssignment and Enrollment
    both hang off of."""

    class_group = models.ForeignKey(
        Class, on_delete=models.CASCADE, related_name="class_subjects"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="class_subjects"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "class_subjects"
        ordering = ["class_group", "subject"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_group", "subject"], name="uniq_subject_per_class"
            )
        ]

    def __str__(self) -> str:
        return f"{self.subject.name} - {self.class_group.name}"


class TeachingAssignment(models.Model):
    """'Assign teachers' (spec §8) / Teacher Dashboard 'My classes',
    'My subjects' (spec §9). Scoped to a Term, not just an AcademicYear,
    since a teacher can be swapped mid-year (e.g. maternity cover) without
    losing the Term 1 assignment history — needed later for accurate
    attendance/marks-entry permission checks per term."""

    class_subject = models.ForeignKey(
        ClassSubject, on_delete=models.CASCADE, related_name="teaching_assignments"
    )
    teacher = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="teaching_assignments",
        limit_choices_to={"employment_status": "ACTIVE"},
    )
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name="teaching_assignments"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teaching_assignments"
        ordering = ["term", "class_subject"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_subject", "term"],
                name="uniq_teacher_per_class_subject_term",
            )
        ]

    def __str__(self) -> str:
        return f"{self.teacher} -> {self.class_subject} ({self.term})"


class Enrollment(models.Model):
    """Student-facing enrollment record (spec §7 'Enrollment'). Distinct
    from Student.current_class (Phase 3) — a student has one current_class
    but potentially many subject enrollments (school-style: all subjects
    for their class; university-style: a chosen subset)."""

    class Status(models.TextChoices):
        ENROLLED = "ENROLLED", "Enrolled"
        DROPPED = "DROPPED", "Dropped"
        COMPLETED = "COMPLETED", "Completed"

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="enrollments"
    )
    class_subject = models.ForeignKey(
        ClassSubject, on_delete=models.CASCADE, related_name="enrollments"
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="enrollments"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.ENROLLED, db_index=True
    )
    enrolled_on = models.DateField(auto_now_add=True)
    dropped_on = models.DateField(blank=True, null=True)

    class Meta:
        db_table = "enrollments"
        ordering = ["-enrolled_on"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "class_subject", "academic_year"],
                name="uniq_enrollment_per_student_subject_year",
            )
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.class_subject} ({self.academic_year})"


# =============================================================================
# Phase 6 — Attendance
# Spec ref §11: teacher marks Present/Absent/Late per class+subject+date;
# Academic Admin can view/correct with "appropriate permissions"; students
# and parents can view. Split into Session (the roll-call event) + Record
# (per-student outcome) so "has today's attendance been taken for this
# class/subject" is a single indexed lookup, and so corrections touch one
# student's row without re-writing the whole day's roll call.
# =============================================================================

class AttendanceSession(models.Model):
    """One roll-call event: a specific ClassSubject on a specific date.
    `taken_by` is the teacher who submitted it — required for the
    TeachingAssignment-based permission check in Phase 9's marking view
    (a teacher may only take attendance for classes/subjects they're
    assigned to teach)."""

    class_subject = models.ForeignKey(
        ClassSubject, on_delete=models.CASCADE, related_name="attendance_sessions"
    )
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name="attendance_sessions"
    )
    date = models.DateField()
    taken_by = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, related_name="attendance_sessions_taken",
        blank=True, null=True,
    )
    is_locked = models.BooleanField(
        default=False,
        help_text="Locked sessions can only be edited by Academic Admin "
                   "corrections (spec §11), not re-submitted by the teacher.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "attendance_sessions"
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_subject", "date"], name="uniq_session_per_class_subject_date"
            )
        ]

    def __str__(self) -> str:
        return f"{self.class_subject} - {self.date}"


class AttendanceRecord(models.Model):
    """Per-student outcome within an AttendanceSession. `recorded_by` is
    updated (not appended) on correction — full before/after values for
    corrections live in AuditLog (spec §5/§27/§33), not duplicated here."""

    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"
        LATE = "LATE", "Late"
        EXCUSED = "EXCUSED", "Excused"

    session = models.ForeignKey(
        AttendanceSession, on_delete=models.CASCADE, related_name="records"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="attendance_records"
    )
    status = models.CharField(max_length=10, choices=Status.choices, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="attendance_records_recorded",
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "attendance_records"
        ordering = ["session", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "student"], name="uniq_record_per_session_student"
            )
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.status} ({self.session.date})"


# =============================================================================
# Phase 7 — Assessment & Grading engine
# Spec refs: §12 (Assessment/Examination — configurable weighting, never
# hard-coded percentages), §13 (Grading Engine — configurable grade bands,
# never hard-coded grades; ranking configurable per school, see
# School.enable_position_ranking above).
# =============================================================================

class AssessmentType(models.Model):
    """Catalog entry — spec §12 examples: CAT, Assignment, Quiz, Midterm,
    End-term Exam, Final Exam, Practical, Project, Continuous Assessment.
    School-scoped (not a hard-coded global enum) so each school can define
    its own set, per §12 'Do not hard-code assessment percentages' /
    'Allow administrators to configure assessment structures'."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="assessment_types")
    name = models.CharField(max_length=100, help_text="e.g. 'CAT', 'Midterm', 'Final Exam'")
    code = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "assessment_types"
        ordering = ["school", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_assessment_type_code_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class AssessmentStructure(models.Model):
    """A named weighting scheme (spec §12 example: CAT1=10%, Assignment=10%,
    CAT2=10%, Midterm=20%, Final=50%). Scoped to a Term so structures can
    differ year to year; `subject` is optional — null means "applies to
    every subject in this term unless a subject-specific structure exists."
    Weight validation (must sum to 100%) is NOT a DB constraint (components
    are added incrementally) — see services.validate_structure_weight()."""

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="assessment_structures"
    )
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name="assessment_structures"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.CASCADE, related_name="assessment_structures",
        blank=True, null=True,
        help_text="Leave blank to apply this structure to all subjects in the term.",
    )
    name = models.CharField(max_length=150, help_text="e.g. 'Standard Term Structure'")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assessment_structures"
        ordering = ["term", "name"]

    def __str__(self) -> str:
        scope = self.subject.name if self.subject else "All subjects"
        return f"{self.name} - {self.term} ({scope})"


class AssessmentComponent(models.Model):
    """One weighted line item within an AssessmentStructure —
    spec §12's 'CAT 1 = 10%' rows."""

    structure = models.ForeignKey(
        AssessmentStructure, on_delete=models.CASCADE, related_name="components"
    )
    assessment_type = models.ForeignKey(
        AssessmentType, on_delete=models.CASCADE, related_name="structure_components"
    )
    weight_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    max_marks = models.DecimalField(
        max_digits=6, decimal_places=2, default=100,
        help_text="Marks this component is scored out of before weighting is applied.",
    )
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "assessment_components"
        ordering = ["structure", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["structure", "assessment_type"],
                name="uniq_assessment_type_per_structure",
            ),
            models.CheckConstraint(
                condition=models.Q(weight_percentage__gte=0) & models.Q(weight_percentage__lte=100),
                name="component_weight_between_0_and_100",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.assessment_type.name} - {self.weight_percentage}%"


class GradingScheme(models.Model):
    """Spec §13: configurable grading scheme, never hard-coded. A school
    may define multiple schemes (e.g. one for school-style A-E grades, a
    different GPA scale for a university-style program)."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="grading_schemes")
    name = models.CharField(max_length=150, help_text="e.g. 'Standard 8-4-4 Grading'")
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "grading_schemes"
        ordering = ["school", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"

    def save(self, *args, **kwargs):
        if self.is_default:
            GradingScheme.objects.filter(
                school=self.school, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class GradeBand(models.Model):
    """Spec §13: min mark, max mark, grade, grade point, remark.
    Example: 80-100 = A = 4.0 points. Overlap between bands within the
    same scheme is checked in services.validate_grade_bands_no_overlap()
    (cross-row validation isn't expressible as a simple DB constraint)."""

    scheme = models.ForeignKey(GradingScheme, on_delete=models.CASCADE, related_name="bands")
    min_mark = models.DecimalField(max_digits=5, decimal_places=2)
    max_mark = models.DecimalField(max_digits=5, decimal_places=2)
    grade = models.CharField(max_length=10, help_text="e.g. 'A', 'B+', 'Pass'")
    grade_point = models.DecimalField(max_digits=3, decimal_places=1, default=0)
    remark = models.CharField(max_length=100, blank=True, help_text="e.g. 'Excellent'")

    class Meta:
        db_table = "grade_bands"
        ordering = ["scheme", "-min_mark"]
        constraints = [
            models.UniqueConstraint(
                fields=["scheme", "grade"], name="uniq_grade_per_scheme"
            ),
            models.CheckConstraint(
                condition=models.Q(max_mark__gte=models.F("min_mark")),
                name="grade_band_max_gte_min",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.grade} ({self.min_mark}-{self.max_mark})"


class Assessment(models.Model):
    """A specific, gradable event — e.g. 'CAT 1' for Grade 10 Mathematics,
    Term 1 2026. Links back to the AssessmentComponent that defines its
    weight, so the grading engine (services.compute_weighted_average) can
    look up how much this assessment counts for."""

    class_subject = models.ForeignKey(
        ClassSubject, on_delete=models.CASCADE, related_name="assessments"
    )
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="assessments")
    component = models.ForeignKey(
        AssessmentComponent, on_delete=models.PROTECT, related_name="assessments",
        help_text="Defines this assessment's weight and max marks.",
    )
    title = models.CharField(max_length=150, help_text="e.g. 'CAT 1 - Algebra'")
    date = models.DateField(blank=True, null=True)
    created_by = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, related_name="assessments_created",
        blank=True, null=True,
    )

    # --- Phase 8: Result Processing Workflow (spec §14) ---
    # DRAFT -> SUBMITTED -> REVIEWED -> VERIFIED -> APPROVED -> PUBLISHED,
    # enforced strictly in order by services.transition_assessment_workflow().
    # `is_published` is kept as a fast boolean flag for read-heavy queries
    # (report cards, student views) and is set automatically only when
    # workflow_status reaches PUBLISHED — never set directly.
    class WorkflowStatus(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        SUBMITTED = "SUBMITTED", "Submitted"
        REVIEWED = "REVIEWED", "Reviewed"
        VERIFIED = "VERIFIED", "Verified"
        APPROVED = "APPROVED", "Approved"
        PUBLISHED = "PUBLISHED", "Published"
        REJECTED = "REJECTED", "Rejected"

    workflow_status = models.CharField(
        max_length=10, choices=WorkflowStatus.choices, default=WorkflowStatus.DRAFT,
        db_index=True,
    )
    submitted_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="assessments_submitted",
        blank=True, null=True,
    )
    submitted_at = models.DateTimeField(blank=True, null=True)
    reviewed_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="assessments_reviewed",
        blank=True, null=True,
        help_text="Department/Class Teacher review step (spec §14).",
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    verified_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="assessments_verified",
        blank=True, null=True,
        help_text="Academic Admin verification step (spec §14).",
    )
    verified_at = models.DateTimeField(blank=True, null=True)
    approved_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="assessments_approved",
        blank=True, null=True,
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    published_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="assessments_published",
        blank=True, null=True,
    )
    published_at = models.DateTimeField(blank=True, null=True)

    is_published = models.BooleanField(
        default=False,
        help_text="Marks visible to students/parents. Set automatically "
                   "when workflow_status reaches PUBLISHED — do not set "
                   "this directly, use services.transition_assessment_workflow().",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assessments"
        ordering = ["-date"]

    def __str__(self) -> str:
        return f"{self.title} - {self.class_subject}"


class AssessmentMark(models.Model):
    """A student's raw score on one Assessment. Marks entered here are raw
    (out of component.max_marks) — weighted contribution to the subject
    total is computed on read by services.compute_weighted_average(),
    never stored redundantly.

    Spec §14 'Once published, prevent unrestricted modification' is
    enforced here, not just by convention: once the parent Assessment's
    workflow_status is PUBLISHED, save() refuses further changes unless
    called with `_bypass_publish_lock=True` — the only caller allowed to
    do that is services.decide_result_amendment()."""

    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE, related_name="marks"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="assessment_marks"
    )
    marks_obtained = models.DecimalField(max_digits=6, decimal_places=2)
    remarks = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="marks_recorded",
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assessment_marks"
        ordering = ["assessment", "student"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "student"], name="uniq_mark_per_assessment_student"
            ),
            models.CheckConstraint(
                condition=models.Q(marks_obtained__gte=0), name="marks_obtained_non_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.assessment} - {self.marks_obtained}"

    def save(self, *args, _bypass_publish_lock: bool = False, **kwargs):
        if self.pk and not _bypass_publish_lock:
            is_published = (
                AssessmentMark.objects.filter(pk=self.pk)
                .values_list("assessment__workflow_status", flat=True)
                .first()
                == Assessment.WorkflowStatus.PUBLISHED
            )
            if is_published:
                raise ValueError(
                    "This mark's assessment has been published; use "
                    "services.request_result_amendment() / "
                    "decide_result_amendment() to change it (spec §14)."
                )
        super().save(*args, **kwargs)


class ResultAmendmentRequest(models.Model):
    """Spec §14: once results are published, prevent unrestricted
    modification — a correction must go through a Result Amendment
    Request with reason, original mark, proposed mark, requesting user,
    date, and approval. Applying the change (services.decide_amendment_
    request) is the only path that mutates a published AssessmentMark."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    assessment_mark = models.ForeignKey(
        AssessmentMark, on_delete=models.CASCADE, related_name="amendment_requests"
    )
    reason = models.TextField()
    original_mark = models.DecimalField(
        max_digits=6, decimal_places=2,
        help_text="Snapshot of the mark at request time, independent of "
                   "whatever the mark is by the time this is reviewed.",
    )
    proposed_mark = models.DecimalField(max_digits=6, decimal_places=2)
    requested_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="amendment_requests_made",
        blank=True, null=True,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reviewed_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="amendment_requests_reviewed",
        blank=True, null=True,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    review_comment = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "result_amendment_requests"
        ordering = ["-requested_at"]

    def __str__(self) -> str:
        return f"{self.assessment_mark} - {self.original_mark} -> {self.proposed_mark} ({self.status})"


# =============================================================================
# Phase 9 — Report Book System
# Spec ref §15: professional, configurable report generation. "Configurable"
# is implemented as structured toggles/choices on ReportTemplate (which
# sections to show, which HTML template file to render with) rather than
# letting schools submit raw template markup — free-text template injection
# would be a server-side template injection risk. The *business logic*
# (services.assemble_report_data) never hard-codes layout; only the
# presentation template (templates/reports/*.html) does, and which file to
# use is itself configurable per school via `template_key`.
# =============================================================================

class ReportTemplate(models.Model):
    """One school may want different layouts for different purposes (e.g.
    a compact primary-school report vs a GPA-heavy senior-school one).
    `template_key` selects a real Django template file — kept as a fixed
    set of known-safe choices rather than an arbitrary path/string to
    avoid template injection."""

    class TemplateKey(models.TextChoices):
        DEFAULT = "DEFAULT", "Default"
        # Additional layouts are added here as new template files ship —
        # never by accepting an arbitrary path from user input.

    school = models.ForeignKey(
        School, on_delete=models.CASCADE, related_name="report_templates"
    )
    name = models.CharField(max_length=150, help_text="e.g. 'Standard Term Report'")
    template_key = models.CharField(
        max_length=20, choices=TemplateKey.choices, default=TemplateKey.DEFAULT
    )
    show_position = models.BooleanField(
        default=True,
        help_text="Also requires School.enable_position_ranking to be True.",
    )
    show_gpa = models.BooleanField(default=False)
    show_attendance = models.BooleanField(default=True)
    footer_text = models.CharField(
        max_length=255, blank=True,
        help_text="e.g. a motto or accreditation line shown at the report footer.",
    )
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "report_templates"
        ordering = ["school", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"

    def save(self, *args, **kwargs):
        if self.is_default:
            ReportTemplate.objects.filter(
                school=self.school, is_default=True
            ).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class ReportCard(models.Model):
    """One generated report for one student in one term. Comments (spec
    §15 'Class teacher comment', 'Principal/head teacher comment') and
    the finalized PDF are persisted here so 'Downloadable report' means
    downloading an actual stored artifact, not re-rendering from
    possibly-changed data after the fact."""

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="report_cards"
    )
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name="report_cards"
    )
    template = models.ForeignKey(
        ReportTemplate, on_delete=models.PROTECT, related_name="report_cards"
    )
    class_teacher_comment = models.TextField(blank=True)
    principal_comment = models.TextField(blank=True)
    pdf_file = models.FileField(
        upload_to="reports/cards/", blank=True, null=True,
        validators=[validate_file_size(20), validate_pdf_content],
    )
    generated_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="report_cards_generated",
        blank=True, null=True,
    )
    generated_at = models.DateTimeField(blank=True, null=True)
    is_finalized = models.BooleanField(
        default=False,
        help_text="Once finalized, regenerating should create a new PDF "
                   "revision rather than silently overwrite (spec §37/§38 "
                   "'never silently change'). Enforcement lands with the "
                   "generation view.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "report_cards"
        ordering = ["-generated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["student", "term", "template"],
                name="uniq_report_card_per_student_term_template",
            )
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.term}"


# =============================================================================
# Phase 10 — Transcript System
# Spec ref §16: cumulative academic record (course history, GPA, CGPA,
# academic/graduation status), generated as a "secure PDF document".
#
# "Secure" isn't elaborated further in the spec, so this implements the
# common, defensible interpretation: each Transcript gets a unique
# verification_code (UUID) and a content_hash so a third party (employer,
# other institution) can confirm authenticity via services.verify_transcript()
# without needing the full document. This is separate from, and doesn't
# preclude, adding PDF password-encryption later if a specific policy is set.
#
# Unlike ReportCard (recomputed fresh from live data each time — a term
# report changes as marks are corrected), Transcript rows are snapshotted
# at generation time into TranscriptEntry. A transcript is closer to a
# permanent record: if grading schemes or subject names change later, a
# previously issued transcript must still read exactly as it did when
# issued.
# =============================================================================

class Transcript(models.Model):
    class GraduationStatus(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        GRADUATED = "GRADUATED", "Graduated"
        NOT_GRADUATED = "NOT_GRADUATED", "Not Graduated"

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="transcripts"
    )
    generated_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="transcripts_generated",
        blank=True, null=True,
    )
    generated_at = models.DateTimeField(auto_now_add=True)

    # Snapshots of student status at generation time — spec §16 "Academic
    # status", "Graduation status". Stored, not read live, so a transcript
    # remains accurate even if the student's status changes afterward.
    academic_status = models.CharField(max_length=15, choices=Student.Status.choices)
    graduation_status = models.CharField(
        max_length=15, choices=GraduationStatus.choices,
        default=GraduationStatus.IN_PROGRESS,
    )

    gpa = models.DecimalField(
        max_digits=4, decimal_places=2, blank=True, null=True,
        help_text="Most recent term/year's grade-point average.",
    )
    cgpa = models.DecimalField(
        max_digits=4, decimal_places=2, blank=True, null=True,
        help_text="Cumulative GPA across every entry on this transcript.",
    )

    verification_code = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, db_index=True,
    )
    content_hash = models.CharField(
        max_length=64, blank=True,
        help_text="SHA-256 of the assembled transcript data at generation "
                   "time, for tamper-evidence alongside verification_code.",
    )
    pdf_file = models.FileField(
        upload_to="transcripts/", blank=True, null=True,
        validators=[validate_file_size(20), validate_pdf_content],
    )

    class Meta:
        db_table = "transcripts"
        ordering = ["-generated_at"]

    def __str__(self) -> str:
        return f"Transcript - {self.student} ({self.generated_at:%Y-%m-%d})"


class TranscriptEntry(models.Model):
    """One historical subject result, snapshotted at the time the parent
    Transcript was generated. `subject` is a soft reference (SET_NULL) for
    querying convenience only — the display fields below are what actually
    render, so renaming/deleting a Subject later cannot alter an already
    issued transcript."""

    transcript = models.ForeignKey(
        Transcript, on_delete=models.CASCADE, related_name="entries"
    )
    subject = models.ForeignKey(
        Subject, on_delete=models.SET_NULL, related_name="transcript_entries",
        blank=True, null=True,
    )
    subject_name = models.CharField(max_length=255)
    academic_year_label = models.CharField(max_length=20)
    term_label = models.CharField(max_length=50)
    score = models.DecimalField(max_digits=6, decimal_places=2)
    grade = models.CharField(max_length=5, blank=True)
    grade_point = models.DecimalField(max_digits=3, decimal_places=2, blank=True, null=True)
    credit_hours = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)

    class Meta:
        db_table = "transcript_entries"
        ordering = ["academic_year_label", "term_label", "subject_name"]

    def __str__(self) -> str:
        return f"{self.subject_name} - {self.term_label} ({self.transcript.student})"


# =============================================================================
# Phase 11 — LMS Module (spec §10)
# Scope note: consistent with Phases 5-8 (Curriculum, Attendance, Assessment,
# Results), this phase builds the data model + business-logic layer.
# Interactive UI (submit-assignment forms, quiz-taking screens, discussion
# threads) is deferred to the Teacher/Student dashboard build-out, same
# pattern used throughout this project.
# =============================================================================

class CourseMaterial(models.Model):
    """Spec §10 'Course content': PDF, Documents, Images, Videos, Links,
    Presentations, Text lessons. One model with a `material_type` discriminator
    rather than one table per type — content types share every other field
    (title, description, ordering, publish state); only the payload differs,
    and only one of file/external_url/text_content is used depending on type."""

    class MaterialType(models.TextChoices):
        PDF = "PDF", "PDF"
        DOCUMENT = "DOCUMENT", "Document"
        IMAGE = "IMAGE", "Image"
        VIDEO = "VIDEO", "Video"
        LINK = "LINK", "Link"
        PRESENTATION = "PRESENTATION", "Presentation"
        TEXT_LESSON = "TEXT_LESSON", "Text Lesson"

    class_subject = models.ForeignKey(
        ClassSubject, on_delete=models.CASCADE, related_name="course_materials"
    )
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="course_materials")
    material_type = models.CharField(max_length=15, choices=MaterialType.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(
        upload_to="lms/materials/", blank=True, null=True,
        validators=[validate_file_size(200), validate_course_material_content],
    )
    external_url = models.URLField(blank=True, help_text="Used when material_type is LINK or VIDEO (e.g. YouTube).")
    text_content = models.TextField(blank=True, help_text="Used when material_type is TEXT_LESSON.")
    uploaded_by = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, related_name="course_materials_uploaded",
        blank=True, null=True,
    )
    order = models.PositiveIntegerField(default=0)
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "course_materials"
        ordering = ["class_subject", "order", "-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_material_type_display()})"


class Assignment(models.Model):
    """Spec §10 'Assignments' — teachers set instructions, deadline, marks,
    submission format; attach resources (AssignmentResource, below)."""

    class SubmissionFormat(models.TextChoices):
        FILE_UPLOAD = "FILE_UPLOAD", "File Upload"
        TEXT_ENTRY = "TEXT_ENTRY", "Text Entry"
        BOTH = "BOTH", "File Upload or Text Entry"

    class_subject = models.ForeignKey(
        ClassSubject, on_delete=models.CASCADE, related_name="assignments"
    )
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="assignments")
    title = models.CharField(max_length=255)
    instructions = models.TextField(blank=True)
    deadline = models.DateTimeField()
    max_marks = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("100"))
    max_attempts = models.PositiveIntegerField(default=1)
    submission_format = models.CharField(
        max_length=15, choices=SubmissionFormat.choices, default=SubmissionFormat.FILE_UPLOAD
    )
    allow_resubmission = models.BooleanField(default=False)
    overdue_reopened = models.BooleanField(
        default=False,
        help_text="Teacher has reopened this overdue assignment for eligible students.",
    )
    created_by = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, related_name="assignments_created",
        blank=True, null=True,
    )
    is_published = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assignments"
        ordering = ["-deadline"]

    def __str__(self) -> str:
        return f"{self.title} ({self.class_subject})"


class AssignmentResource(models.Model):
    """Spec §10 'Attach resources'. Kept separate from CourseMaterial —
    a resource attached to one specific assignment isn't part of the
    general course content library and shouldn't appear there."""

    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name="resources"
    )
    title = models.CharField(max_length=255)
    file = models.FileField(
        upload_to="lms/assignment_resources/", blank=True, null=True,
        validators=[validate_file_size(200), validate_course_material_content],
    )
    external_url = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "assignment_resources"
        ordering = ["assignment", "id"]

    def __str__(self) -> str:
        return self.title


class AssignmentSubmission(models.Model):
    """One row per student per assignment — the CURRENT submission.
    Resubmission (spec §10 'Resubmit where permitted') overwrites this row
    via services.submit_assignment(), which increments `attempt_number` and
    audit-logs the previous content rather than keeping separate rows, since
    only the latest attempt is ever gradeable."""

    class Status(models.TextChoices):
        SUBMITTED = "SUBMITTED", "Submitted"
        RESUBMITTED = "RESUBMITTED", "Resubmitted"
        GRADED = "GRADED", "Graded"

    assignment = models.ForeignKey(
        Assignment, on_delete=models.CASCADE, related_name="submissions"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="assignment_submissions"
    )
    submitted_file = models.FileField(
        upload_to="lms/submissions/", blank=True, null=True,
        validators=[validate_file_size(25), validate_document_content],
    )
    submitted_text = models.TextField(blank=True)
    attempt_number = models.PositiveIntegerField(default=1)
    is_late = models.BooleanField(default=False)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.SUBMITTED)
    marks_obtained = models.DecimalField(max_digits=6, decimal_places=2, blank=True, null=True)
    feedback = models.TextField(blank=True)
    graded_by = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, related_name="submissions_graded",
        blank=True, null=True,
    )
    graded_at = models.DateTimeField(blank=True, null=True)
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assignment_submissions"
        ordering = ["-submitted_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["assignment", "student"], name="uniq_submission_per_assignment_student"
            )
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.assignment} (attempt {self.attempt_number})"


# -----------------------------------------------------------------------
# Quizzes (spec §10): MCQ, True/False, Short answer, Multiple-answer.
# Automatic marking where appropriate — objective question types
# (MCQ/TRUE_FALSE/MULTIPLE_ANSWER) auto-grade; SHORT_ANSWER always needs
# manual grading since free text can't be reliably auto-marked.
# -----------------------------------------------------------------------

class Quiz(models.Model):
    class TaskCategory(models.TextChoices):
        QUIZ = "QUIZ", "Quiz"
        CAT = "CAT", "CAT"
        EXAM = "EXAM", "Exam"

    class SubmissionFormat(models.TextChoices):
        TEXT_ENTRY = "TEXT_ENTRY", "Type answers"
        FILE_UPLOAD = "FILE_UPLOAD", "Upload document"
        BOTH = "BOTH", "Type answers or upload document"

    class_subject = models.ForeignKey(
        ClassSubject, on_delete=models.CASCADE, related_name="quizzes"
    )
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="quizzes")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    max_marks = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("100"),
        help_text="Task score is normalized against this maximum; defaults to 100.",
    )
    task_category = models.CharField(
        max_length=10, choices=TaskCategory.choices, default=TaskCategory.QUIZ,
        db_index=True,
    )
    submission_format = models.CharField(
        max_length=15, choices=SubmissionFormat.choices,
        default=SubmissionFormat.TEXT_ENTRY,
    )
    question_file = models.FileField(
        upload_to="lms/assessment_questions/", blank=True, null=True,
        validators=[validate_file_size(5), validate_course_material_content],
    )
    deadline = models.DateTimeField(blank=True, null=True)
    time_limit_minutes = models.PositiveIntegerField(blank=True, null=True)
    max_attempts = models.PositiveIntegerField(default=1)
    overdue_reopened = models.BooleanField(
        default=False,
        help_text="Teacher has reopened this overdue task for eligible students.",
    )
    is_published = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, related_name="quizzes_created",
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "quizzes"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class QuizQuestion(models.Model):
    class QuestionType(models.TextChoices):
        MULTIPLE_CHOICE = "MULTIPLE_CHOICE", "Multiple Choice"
        TRUE_FALSE = "TRUE_FALSE", "True/False"
        SHORT_ANSWER = "SHORT_ANSWER", "Short Answer"
        MULTIPLE_ANSWER = "MULTIPLE_ANSWER", "Multiple Answer"

    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    question_text = models.TextField()
    question_type = models.CharField(max_length=20, choices=QuestionType.choices)
    marks = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("1.00"))
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "quiz_questions"
        ordering = ["quiz", "order"]

    def __str__(self) -> str:
        return f"{self.question_text[:50]} ({self.get_question_type_display()})"

    @property
    def requires_manual_grading(self) -> bool:
        return self.question_type == self.QuestionType.SHORT_ANSWER


class QuizOption(models.Model):
    """Answer choice for MULTIPLE_CHOICE / TRUE_FALSE / MULTIPLE_ANSWER
    questions. Not used for SHORT_ANSWER."""

    question = models.ForeignKey(
        QuizQuestion, on_delete=models.CASCADE, related_name="options"
    )
    option_text = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "quiz_options"
        ordering = ["question", "order"]

    def __str__(self) -> str:
        return self.option_text


class QuizAttempt(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts")
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="quiz_attempts"
    )
    attempt_number = models.PositiveIntegerField(default=1)
    started_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(blank=True, null=True)
    auto_score = models.DecimalField(
        max_digits=6, decimal_places=2, blank=True, null=True,
        help_text="Sum of marks from auto-gradable questions only.",
    )
    manual_score = models.DecimalField(
        max_digits=6, decimal_places=2, blank=True, null=True,
        help_text="Sum of marks from manually-graded (short-answer) questions.",
    )
    is_fully_graded = models.BooleanField(
        default=False,
        help_text="True once every short-answer question (if any) has been "
                   "manually graded, making the total score final.",
    )

    class Meta:
        db_table = "quiz_attempts"
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["quiz", "student", "attempt_number"],
                name="uniq_attempt_number_per_quiz_student",
            )
        ]

    def __str__(self) -> str:
        return f"{self.student} - {self.quiz} (attempt {self.attempt_number})"

    @property
    def total_score(self):
        if self.auto_score is None and self.manual_score is None:
            return None
        scores = [score for score in (self.auto_score, self.manual_score) if score is not None]
        return (sum(scores, Decimal("0")) / Decimal(len(scores))).quantize(Decimal("0.01"))


class QuizAnswer(models.Model):
    """One row per question answered within an attempt. `selected_options`
    covers MCQ/TRUE_FALSE/MULTIPLE_ANSWER; `text_answer` covers SHORT_ANSWER."""

    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name="answers")
    question = models.ForeignKey(QuizQuestion, on_delete=models.CASCADE, related_name="answers")
    selected_options = models.ManyToManyField(QuizOption, blank=True, related_name="selected_in_answers")
    text_answer = models.TextField(blank=True)
    submitted_file = models.FileField(
        upload_to="lms/quiz_submissions/", blank=True, null=True,
        validators=[validate_file_size(25), validate_document_content],
    )
    marks_awarded = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)

    class Meta:
        db_table = "quiz_answers"
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "question"], name="uniq_answer_per_attempt_question"
            )
        ]

    def __str__(self) -> str:
        return f"{self.attempt} - {self.question}"


# -----------------------------------------------------------------------
# Discussions (spec §10): course discussions, teacher announcements,
# student questions, replies.
# -----------------------------------------------------------------------

class Discussion(models.Model):
    class ThreadType(models.TextChoices):
        ANNOUNCEMENT = "ANNOUNCEMENT", "Announcement"
        DISCUSSION = "DISCUSSION", "Discussion"
        QUESTION = "QUESTION", "Question"

    class_subject = models.ForeignKey(
        ClassSubject, on_delete=models.CASCADE, related_name="discussions"
    )
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="discussions")
    thread_type = models.CharField(max_length=15, choices=ThreadType.choices)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="discussions_created",
        blank=True, null=True,
    )
    is_pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "discussions"
        ordering = ["-is_pinned", "-created_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.get_thread_type_display()})"


class DiscussionReply(models.Model):
    discussion = models.ForeignKey(
        Discussion, on_delete=models.CASCADE, related_name="replies"
    )
    author = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="discussion_replies",
        blank=True, null=True,
    )
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "discussion_replies"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"Reply by {self.author} on {self.discussion}"


# =============================================================================
# Phase 12 — Finance (spec §19, §23, §38)
#
# §23 'Financial and Academic Separation': Finance Admin must see fees,
# payments, invoices, financial reporting — never academic grades — and
# must see only the minimum student identity needed to process accounts.
# These models don't FK into Assessment/AssessmentMark/GradingScheme at
# all, by design, so a Finance Admin-scoped view/serializer can never
# accidentally join into academic data. Enforcement of the *view-layer*
# boundary (which fields a Finance Admin's screens expose) lands with the
# Finance Admin dashboard build-out; this data layer just makes the leak
# structurally impossible rather than policy-only.
#
# §38 'Financial Data Integrity': "Payments should never be casually
# deleted. Prefer reversal/refund/adjustment/correction over destructive
# deletion." Payment and Invoice have no soft-delete/cancel path that
# removes rows — Refund and FinancialAdjustment are additive, linked
# records instead. "Every financial transaction should have a unique
# identifier" / "Receipts should have unique numbers" -> invoice_number,
# payment_number, and receipt_number are all unique, auto-generated in
# save() if left blank.
# =============================================================================

def _generate_unique_code(prefix: str) -> str:
    """Simple collision-safe identifier generator used for invoice/payment/
    receipt numbers. Not strictly sequential (a sequential-per-school
    counter would need row locking to stay race-safe under concurrent
    writes) — uniqueness and human-legibility matter more here than
    sequential ordering, per spec §38's actual requirement."""
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


class FeeCategory(models.Model):
    """Spec §19 'Fee categories' — e.g. Tuition, Transport, Boarding, Exam Fee."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="fee_categories")
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=20)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "fee_categories"
        ordering = ["school", "name"]
        verbose_name_plural = "Fee categories"
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"], name="uniq_fee_category_code_per_school"
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class FeeStructure(models.Model):
    """Spec §19 'Fee structures' scoped by Academic Year / Term / Class or
    Program — a named, reusable billing template. `class_group`/`program`
    are both optional: leaving both blank means the structure applies
    school-wide for that year/term."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="fee_structures")
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="fee_structures"
    )
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name="fee_structures", blank=True, null=True
    )
    class_group = models.ForeignKey(
        Class, on_delete=models.SET_NULL, related_name="fee_structures", blank=True, null=True
    )
    program = models.ForeignKey(
        Program, on_delete=models.SET_NULL, related_name="fee_structures", blank=True, null=True
    )
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fee_structures"
        ordering = ["-academic_year", "name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.academic_year})"


class FeeStructureItem(models.Model):
    structure = models.ForeignKey(
        FeeStructure, on_delete=models.CASCADE, related_name="items"
    )
    category = models.ForeignKey(
        FeeCategory, on_delete=models.PROTECT, related_name="structure_items"
    )
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    is_mandatory = models.BooleanField(default=True)

    class Meta:
        db_table = "fee_structure_items"
        ordering = ["structure", "category"]
        constraints = [
            models.UniqueConstraint(
                fields=["structure", "category"], name="uniq_category_per_structure"
            ),
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="fee_item_amount_non_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.category.name} - {self.amount} ({self.structure.name})"


class FeeConcession(models.Model):
    """Spec §19 'Discounts', 'Scholarships', 'Waivers' — unified under one
    model with a type discriminator since all three reduce what a
    specific student owes and share the same approval/audit shape; only
    the label and typical reason differ."""

    class ConcessionType(models.TextChoices):
        DISCOUNT = "DISCOUNT", "Discount"
        SCHOLARSHIP = "SCHOLARSHIP", "Scholarship"
        WAIVER = "WAIVER", "Waiver"

    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="fee_concessions"
    )
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="fee_concessions"
    )
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name="fee_concessions", blank=True, null=True
    )
    concession_type = models.CharField(max_length=15, choices=ConcessionType.choices)
    description = models.CharField(max_length=255, blank=True)
    percentage = models.DecimalField(
        max_digits=5, decimal_places=2, blank=True, null=True,
        help_text="Use either percentage OR fixed_amount, not both.",
    )
    fixed_amount = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    approved_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="fee_concessions_approved",
        blank=True, null=True,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "fee_concessions"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(percentage__isnull=False, fixed_amount__isnull=True)
                    | models.Q(percentage__isnull=True, fixed_amount__isnull=False)
                ),
                name="fee_concession_exactly_one_of_percentage_or_amount",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_concession_type_display()} - {self.student} ({self.academic_year})"


class Invoice(models.Model):
    """Spec §19 'Invoices'. `total_amount` is a stored snapshot of the sum
    of InvoiceLineItem amounts at generation time — not recomputed live —
    so an invoice a student already started paying against can't silently
    change total if fee structure amounts are edited afterward."""

    class Status(models.TextChoices):
        UNPAID = "UNPAID", "Unpaid"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially Paid"
        PAID = "PAID", "Paid"
        OVERDUE = "OVERDUE", "Overdue"
        CANCELLED = "CANCELLED", "Cancelled"

    invoice_number = models.CharField(max_length=30, unique=True, editable=False, blank=True)
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="invoices")
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="invoices")
    academic_year = models.ForeignKey(
        AcademicYear, on_delete=models.CASCADE, related_name="invoices"
    )
    term = models.ForeignKey(
        Term, on_delete=models.CASCADE, related_name="invoices", blank=True, null=True
    )
    fee_structure = models.ForeignKey(
        FeeStructure, on_delete=models.PROTECT, related_name="invoices"
    )
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.UNPAID, db_index=True)
    issue_date = models.DateField()
    due_date = models.DateField()
    created_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="invoices_created",
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "invoices"
        ordering = ["-issue_date"]
        constraints = [
            models.CheckConstraint(condition=models.Q(total_amount__gte=0), name="invoice_total_non_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.invoice_number} - {self.student}"

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            self.invoice_number = _generate_unique_code("INV")
        super().save(*args, **kwargs)


class InvoiceLineItem(models.Model):
    """A positive FEE line or a negative-effect DISCOUNT/SCHOLARSHIP/
    WAIVER/ADJUSTMENT line. `amount` is always stored positive; `line_type`
    determines whether it adds to or subtracts from the invoice total —
    keeps the arithmetic explicit rather than relying on sign conventions
    that are easy to get backwards."""

    class LineType(models.TextChoices):
        FEE = "FEE", "Fee"
        DISCOUNT = "DISCOUNT", "Discount"
        SCHOLARSHIP = "SCHOLARSHIP", "Scholarship"
        WAIVER = "WAIVER", "Waiver"
        ADJUSTMENT = "ADJUSTMENT", "Adjustment"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="line_items")
    category = models.ForeignKey(
        FeeCategory, on_delete=models.SET_NULL, related_name="invoice_line_items",
        blank=True, null=True,
    )
    line_type = models.CharField(max_length=15, choices=LineType.choices, default=LineType.FEE)
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        db_table = "invoice_line_items"
        ordering = ["invoice", "id"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gte=0), name="invoice_line_amount_non_negative"),
        ]

    def __str__(self) -> str:
        return f"{self.get_line_type_display()}: {self.amount} ({self.invoice.invoice_number})"

    @property
    def signed_amount(self):
        return -self.amount if self.line_type != self.LineType.FEE else self.amount


class Payment(models.Model):
    """Spec §19 'Payments', 'Partial payments' — one Invoice can have many
    Payments. §19 'Architect for: Cash, Bank, Card, Mobile money, M-Pesa/
    Daraja, Other payment gateways' — `payment_method` covers the type;
    `gateway_reference` holds the external transaction ID (e.g. an M-Pesa
    Daraja checkout receipt) for gateway-based methods, left blank for cash."""

    class Method(models.TextChoices):
        CASH = "CASH", "Cash"
        BANK_TRANSFER = "BANK_TRANSFER", "Bank Transfer"
        CARD = "CARD", "Card"
        MOBILE_MONEY = "MOBILE_MONEY", "Mobile Money"
        MPESA = "MPESA", "M-Pesa / Daraja"
        OTHER_GATEWAY = "OTHER_GATEWAY", "Other Payment Gateway"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        REVERSED = "REVERSED", "Reversed"

    payment_number = models.CharField(max_length=30, unique=True, editable=False, blank=True)
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=15, choices=Method.choices)
    gateway_reference = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.COMPLETED)
    payer_name = models.CharField(
        max_length=150, blank=True,
        help_text="Who physically paid (may differ from the student), e.g. a guardian's name.",
    )
    received_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="payments_received",
        blank=True, null=True,
    )
    notes = models.CharField(max_length=255, blank=True)
    payment_date = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "payments"
        ordering = ["-payment_date"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payment_amount_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.payment_number} - {self.amount} ({self.invoice.invoice_number})"

    def save(self, *args, **kwargs):
        if not self.payment_number:
            self.payment_number = _generate_unique_code("PAY")
        super().save(*args, **kwargs)


class Receipt(models.Model):
    """Spec §38 'Receipts should have unique numbers'. One Receipt per
    completed Payment, generated automatically by
    services.record_payment() — never created manually, so a receipt
    can't exist without a real underlying payment."""

    receipt_number = models.CharField(max_length=30, unique=True, editable=False, blank=True)
    payment = models.OneToOneField(Payment, on_delete=models.PROTECT, related_name="receipt")
    issued_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="receipts_issued",
        blank=True, null=True,
    )
    issued_at = models.DateTimeField(auto_now_add=True)
    pdf_file = models.FileField(
        upload_to="finance/receipts/", blank=True, null=True,
        validators=[validate_file_size(20), validate_pdf_content],
    )

    class Meta:
        db_table = "receipts"
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        return self.receipt_number

    def save(self, *args, **kwargs):
        if not self.receipt_number:
            self.receipt_number = _generate_unique_code("RCT")
        super().save(*args, **kwargs)


class Refund(models.Model):
    """Spec §19 'Refunds', §38 'Prefer reversal/refund ... over destructive
    deletion' — a Refund is always a new linked record against an existing
    Payment, never a deletion or edit of the Payment itself."""

    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        COMPLETED = "COMPLETED", "Completed"

    refund_number = models.CharField(max_length=30, unique=True, editable=False, blank=True)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.REQUESTED)
    requested_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="refunds_requested",
        blank=True, null=True,
    )
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="refunds_decided",
        blank=True, null=True,
    )
    decided_at = models.DateTimeField(blank=True, null=True)
    refund_method = models.CharField(max_length=15, choices=Payment.Method.choices, blank=True)
    reference_number = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "refunds"
        ordering = ["-requested_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="refund_amount_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.refund_number} - {self.amount} ({self.status})"

    def save(self, *args, **kwargs):
        if not self.refund_number:
            self.refund_number = _generate_unique_code("RFD")
        super().save(*args, **kwargs)


class FinancialAdjustment(models.Model):
    """Spec §19 'Adjustments' / §38 'Prefer ... correction over destructive
    deletion'. A signed correction applied to an Invoice's effective
    balance without editing the invoice's original line items — so the
    original billed amount always remains visible/auditable."""

    class AdjustmentType(models.TextChoices):
        CORRECTION = "CORRECTION", "Correction"
        WRITE_OFF = "WRITE_OFF", "Write-off"
        OTHER = "OTHER", "Other"

    invoice = models.ForeignKey(
        Invoice, on_delete=models.CASCADE, related_name="adjustments"
    )
    adjustment_type = models.CharField(max_length=15, choices=AdjustmentType.choices)
    amount = models.DecimalField(
        max_digits=10, decimal_places=2,
        help_text="Positive increases the balance owed, negative decreases it.",
    )
    reason = models.TextField()
    created_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="financial_adjustments_created",
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "financial_adjustments"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_adjustment_type_display()} {self.amount} ({self.invoice.invoice_number})"


# =============================================================================
# Phase 13 — Library Module (spec §20)
# =============================================================================

class LibrarySettings(models.Model):
    """Spec §3 'do not hard-code' — loan period and fine rate are
    school-configurable rather than baked into borrow/return logic."""

    school = models.OneToOneField(
        School, on_delete=models.CASCADE, related_name="library_settings"
    )
    loan_period_days = models.PositiveIntegerField(default=14)
    max_books_per_student = models.PositiveIntegerField(default=3)
    fine_per_day = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        db_table = "library_settings"
        verbose_name_plural = "Library settings"

    def __str__(self) -> str:
        return f"Library settings ({self.school.code})"


class BookCategory(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="book_categories")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        db_table = "book_categories"
        ordering = ["school", "name"]
        verbose_name_plural = "Book categories"
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="uniq_book_category_per_school")
        ]

    def __str__(self) -> str:
        return self.name


class Author(models.Model):
    """Not school-scoped — an author's identity is reference data shared
    across the whole catalog, not something a specific school owns."""

    name = models.CharField(max_length=255)
    bio = models.TextField(blank=True)

    class Meta:
        db_table = "authors"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Publisher(models.Model):
    """Not school-scoped, same reasoning as Author."""

    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)

    class Meta:
        db_table = "publishers"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Book(models.Model):
    """A catalog entry (title/edition), not a physical item — physical
    items are BookCopy, below. One Book can have many BookCopy rows."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="books")
    title = models.CharField(max_length=255)
    isbn = models.CharField(max_length=20, blank=True)
    category = models.ForeignKey(
        BookCategory, on_delete=models.SET_NULL, related_name="books", blank=True, null=True
    )
    publisher = models.ForeignKey(
        Publisher, on_delete=models.SET_NULL, related_name="books", blank=True, null=True
    )
    authors = models.ManyToManyField(Author, blank=True, related_name="books")
    publication_year = models.PositiveIntegerField(blank=True, null=True)
    edition = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    cover_image = models.ImageField(
        upload_to="library/covers/", blank=True, null=True,
        validators=[validate_file_size(5), validate_image_content],
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "books"
        ordering = ["title"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "isbn"],
                condition=models.Q(isbn__gt=""),
                name="uniq_isbn_per_school_when_set",
            )
        ]

    def __str__(self) -> str:
        return self.title


class BookCopy(models.Model):
    """Spec §20 'Copies' — one physical item on the shelf. Multiple copies
    of the same Book each get their own accession number and lifecycle
    (a copy can be lost/damaged/withdrawn independently of its siblings)."""

    class Condition(models.TextChoices):
        NEW = "NEW", "New"
        GOOD = "GOOD", "Good"
        FAIR = "FAIR", "Fair"
        POOR = "POOR", "Poor"
        DAMAGED = "DAMAGED", "Damaged"

    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        BORROWED = "BORROWED", "Borrowed"
        RESERVED = "RESERVED", "Reserved"
        LOST = "LOST", "Lost"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="copies")
    accession_number = models.CharField(max_length=30)
    condition = models.CharField(max_length=10, choices=Condition.choices, default=Condition.GOOD)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.AVAILABLE, db_index=True)
    shelf_location = models.CharField(max_length=100, blank=True)
    acquired_date = models.DateField(blank=True, null=True)

    class Meta:
        db_table = "book_copies"
        ordering = ["book", "accession_number"]
        verbose_name_plural = "Book copies"
        constraints = [
            models.UniqueConstraint(
                fields=["book", "accession_number"], name="uniq_accession_number_per_book"
            )
        ]

    def __str__(self) -> str:
        return f"{self.book.title} - {self.accession_number}"


class Borrowing(models.Model):
    """Spec §20 'Students', 'Staff', 'Borrowing', 'Returns', 'Due dates',
    'Fines'. Borrower is exactly one of `student` or `staff` (constraint
    below) — both roles can borrow, per the spec's explicit listing of
    both under this module."""

    class Status(models.TextChoices):
        BORROWED = "BORROWED", "Borrowed"
        RETURNED = "RETURNED", "Returned"
        LOST = "LOST", "Lost"

    book_copy = models.ForeignKey(
        BookCopy, on_delete=models.PROTECT, related_name="borrowings"
    )
    student = models.ForeignKey(
        Student, on_delete=models.CASCADE, related_name="library_borrowings",
        blank=True, null=True,
    )
    staff = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="library_borrowings",
        blank=True, null=True,
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.BORROWED, db_index=True)
    borrowed_date = models.DateField()
    due_date = models.DateField()
    returned_date = models.DateField(blank=True, null=True)
    fine_amount = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0.00"))
    fine_paid = models.BooleanField(default=False)
    issued_by = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, related_name="borrowings_issued",
        blank=True, null=True, help_text="The librarian/staff who issued the copy.",
    )
    returned_to = models.ForeignKey(
        Staff, on_delete=models.SET_NULL, related_name="borrowings_received",
        blank=True, null=True,
    )
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "library_borrowings"
        ordering = ["-borrowed_date"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(student__isnull=False, staff__isnull=True)
                    | models.Q(student__isnull=True, staff__isnull=False)
                ),
                name="borrowing_exactly_one_of_student_or_staff",
            ),
            models.UniqueConstraint(
                fields=["book_copy"],
                condition=models.Q(returned_date__isnull=True),
                name="uniq_active_borrowing_per_copy",
            ),
            models.CheckConstraint(
                condition=models.Q(fine_amount__gte=0), name="library_fine_non_negative"
            ),
        ]

    def __str__(self) -> str:
        borrower = self.student or self.staff
        return f"{self.book_copy} - {borrower} ({self.status})"


# =============================================================================
# Phase 14 — Timetable Module (spec §21)
# Design: TimetableSlot reuses TeachingAssignment (Phase 5) rather than
# taking separate teacher/class_subject inputs — a lesson can only be
# scheduled for a pairing that's already a validated "this teacher teaches
# this subject to this class this term" record. `teacher`/`class_group`/
# `term` are denormalized copies auto-populated from teaching_assignment
# in save() (never set independently) purely so Django can express real
# DB-level UniqueConstraints on them — composite constraints can't reach
# through a FK join, so this redundancy buys genuine double-booking
# prevention at the database layer, not just an application-level check.
# =============================================================================

class Room(models.Model):
    class RoomType(models.TextChoices):
        CLASSROOM = "CLASSROOM", "Classroom"
        LAB = "LAB", "Laboratory"
        LIBRARY = "LIBRARY", "Library"
        HALL = "HALL", "Hall"
        OTHER = "OTHER", "Other"

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="rooms")
    name = models.CharField(max_length=100)
    room_type = models.CharField(max_length=10, choices=RoomType.choices, default=RoomType.CLASSROOM)
    capacity = models.PositiveIntegerField(default=0, help_text="0 = not tracked")
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "rooms"
        ordering = ["school", "name"]
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="uniq_room_name_per_school")
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.school.code})"


class Period(models.Model):
    """A named timeslot (e.g. 'Period 1', 8:00-8:40). `is_break` marks
    non-teaching slots (break/lunch) so they can be excluded from
    scheduling without deleting them from the daily structure display."""

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="periods")
    name = models.CharField(max_length=50)
    start_time = models.TimeField()
    end_time = models.TimeField()
    order = models.PositiveIntegerField(default=0)
    is_break = models.BooleanField(default=False)

    class Meta:
        db_table = "periods"
        ordering = ["school", "order"]
        constraints = [
            models.UniqueConstraint(fields=["school", "name"], name="uniq_period_name_per_school"),
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="period_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.start_time}-{self.end_time})"


class TimetableSlot(models.Model):
    class DayOfWeek(models.TextChoices):
        MONDAY = "MON", "Monday"
        TUESDAY = "TUE", "Tuesday"
        WEDNESDAY = "WED", "Wednesday"
        THURSDAY = "THU", "Thursday"
        FRIDAY = "FRI", "Friday"
        SATURDAY = "SAT", "Saturday"

    teaching_assignment = models.ForeignKey(
        TeachingAssignment, on_delete=models.CASCADE, related_name="timetable_slots"
    )
    room = models.ForeignKey(
        Room, on_delete=models.SET_NULL, related_name="timetable_slots", blank=True, null=True
    )
    day_of_week = models.CharField(max_length=3, choices=DayOfWeek.choices)
    period = models.ForeignKey(Period, on_delete=models.CASCADE, related_name="timetable_slots")

    # Denormalized from teaching_assignment — see module docstring above.
    # Never set these directly; save() derives them every time.
    term = models.ForeignKey(Term, on_delete=models.CASCADE, related_name="timetable_slots", editable=False)
    teacher = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="timetable_slots", editable=False
    )
    class_group = models.ForeignKey(
        Class, on_delete=models.CASCADE, related_name="timetable_slots", editable=False
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "timetable_slots"
        ordering = ["term", "day_of_week", "period"]
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "term", "day_of_week", "period"],
                name="uniq_teacher_timetable_slot",
            ),
            models.UniqueConstraint(
                fields=["room", "term", "day_of_week", "period"],
                condition=models.Q(room__isnull=False),
                name="uniq_room_timetable_slot",
            ),
            models.UniqueConstraint(
                fields=["class_group", "term", "day_of_week", "period"],
                name="uniq_class_timetable_slot",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.class_group} - {self.teaching_assignment.class_subject.subject} - {self.get_day_of_week_display()} {self.period}"

    def save(self, *args, **kwargs):
        self.term = self.teaching_assignment.term
        self.teacher = self.teaching_assignment.teacher
        self.class_group = self.teaching_assignment.class_subject.class_group
        super().save(*args, **kwargs)


# =============================================================================
# Phase 15 — Communication Module (spec §22)
# Spec: "SMS-ready architecture", "Push notification-ready architecture" —
# the schema below supports these channels; only IN_APP and EMAIL actually
# send anything (Django's own mail backend). SMS/PUSH create a
# NotificationDelivery row with status PENDING and stay unsent until real
# gateway credentials (e.g. Africa's Talking, Twilio, FCM) are configured —
# same honest "architected, not fabricated" approach used for M-Pesa in
# Phase 12. See services.send_notification() docstring for specifics.
# =============================================================================

class NotificationPreference(models.Model):
    """Per-user opt-in/out per channel. IN_APP defaults on (it's the core,
    zero-cost channel); SMS/PUSH default off since no live gateway is
    configured yet — flipping them on before a gateway exists would just
    silently queue undeliverable messages."""

    user = models.OneToOneField(
        "smsApp.User", on_delete=models.CASCADE, related_name="notification_preference"
    )
    in_app_enabled = models.BooleanField(default=True)
    email_enabled = models.BooleanField(default=True)
    sms_enabled = models.BooleanField(default=False)
    push_enabled = models.BooleanField(default=False)

    class Meta:
        db_table = "notification_preferences"

    def __str__(self) -> str:
        return f"Notification prefs - {self.user}"


class Announcement(models.Model):
    """Spec §22 'Announcements' — school-wide, not tied to one class or
    subject (course-level announcements already exist as
    Discussion(thread_type=ANNOUNCEMENT) from Phase 11). `audience` drives
    which roles a Notification fan-out targets in
    services.create_announcement()."""

    class Audience(models.TextChoices):
        ALL = "ALL", "Everyone"
        STUDENTS = "STUDENTS", "Students"
        PARENTS = "PARENTS", "Parents/Guardians"
        STAFF = "STAFF", "All Staff"
        TEACHERS = "TEACHERS", "Teachers"

    school = models.ForeignKey(School, on_delete=models.CASCADE, related_name="announcements")
    title = models.CharField(max_length=255)
    body = models.TextField()
    audience = models.CharField(max_length=10, choices=Audience.choices, default=Audience.ALL)
    created_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="announcements_created",
        blank=True, null=True,
    )
    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(blank=True, null=True)
    expires_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "announcements"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.title


class Notification(models.Model):
    """Spec §22 'In-app notifications' + the role-aware examples given
    (student result-published, parent report-available, finance
    payment-received, teacher deadline-approaching). One row per
    recipient per event — a broadcast announcement to 500 students
    produces 500 Notification rows, not one shared row, so each
    student's read/unread state is independent."""

    class NotificationType(models.TextChoices):
        RESULT_PUBLISHED = "RESULT_PUBLISHED", "Result Published"
        REPORT_AVAILABLE = "REPORT_AVAILABLE", "Report Available"
        PAYMENT_RECEIVED = "PAYMENT_RECEIVED", "Payment Received"
        ASSIGNMENT_DEADLINE = "ASSIGNMENT_DEADLINE", "Assignment Deadline"
        ANNOUNCEMENT = "ANNOUNCEMENT", "Announcement"
        ATTENDANCE = "ATTENDANCE", "Attendance"
        TASK_ADDED = "TASK_ADDED", "Task Added"
        SUBMISSION_RECEIVED = "SUBMISSION_RECEIVED", "Submission Received"
        OTHER = "OTHER", "Other"

    recipient = models.ForeignKey(
        "smsApp.User", on_delete=models.CASCADE, related_name="notifications"
    )
    notification_type = models.CharField(
        max_length=25, choices=NotificationType.choices, db_index=True
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    related_model = models.CharField(
        max_length=100, blank=True, help_text="e.g. 'Invoice', 'ReportCard' — for click-through."
    )
    related_object_id = models.CharField(max_length=50, blank=True)
    is_read = models.BooleanField(default=False, db_index=True)
    read_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "notifications"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "created_at"], name="notif_recipient_read_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.recipient} - {self.title}"


class NotificationDelivery(models.Model):
    """One row per (Notification, channel) delivery attempt. Splitting
    this from Notification itself lets one logical notification fan out
    across multiple channels (in-app + email, say) with independent
    success/failure tracking per channel."""

    class Channel(models.TextChoices):
        IN_APP = "IN_APP", "In-App"
        EMAIL = "EMAIL", "Email"
        SMS = "SMS", "SMS"
        PUSH = "PUSH", "Push"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="deliveries"
    )
    channel = models.CharField(max_length=10, choices=Channel.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider_reference = models.CharField(
        max_length=255, blank=True, help_text="External message ID once a real gateway is wired up."
    )
    error_message = models.CharField(max_length=255, blank=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "notification_deliveries"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "channel"], name="uniq_delivery_per_notification_channel"
            )
        ]

    def __str__(self) -> str:
        return f"{self.notification} via {self.channel} ({self.status})"


# =============================================================================
# Phase 20 — Staff Admin Dashboard (spec §6)
#
# Scope note: 'Staff management' (create/edit/deactivate, profiles, IDs,
# departments, job titles, employment status, qualifications, documents,
# emergency contacts) reuses Staff/StaffQualification/Department entirely
# from Phase 3 — no new models needed there. 'Workload' reuses
# TeachingAssignment/TimetableSlot from Phase 5/14. Only 'Staff attendance'
# and 'Leave management' need new models, below. 'Staff performance'
# (appraisals, training records) is explicitly deferred — a distinct HR
# feature area, not required for the rest of this phase to function.
# =============================================================================

class StaffAttendanceRecord(models.Model):
    """Spec §6 'Staff attendance': attendance/absence/late arrival/early
    departure. One row per staff member per day (unlike student
    attendance's per-class-period Session+Record split from Phase 6 —
    staff attendance is a single daily check-in/out, not per-lesson)."""

    class Status(models.TextChoices):
        PRESENT = "PRESENT", "Present"
        ABSENT = "ABSENT", "Absent"
        LATE = "LATE", "Late Arrival"
        EARLY_DEPARTURE = "EARLY_DEPARTURE", "Early Departure"
        ON_LEAVE = "ON_LEAVE", "On Leave"

    staff = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="attendance_records"
    )
    date = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, db_index=True)
    check_in_time = models.TimeField(blank=True, null=True)
    check_out_time = models.TimeField(blank=True, null=True)
    notes = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="staff_attendance_recorded",
        blank=True, null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "staff_attendance_records"
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["staff", "date"], name="uniq_staff_attendance_per_day"
            )
        ]

    def __str__(self) -> str:
        return f"{self.staff} - {self.date} - {self.status}"


class LeaveRequest(models.Model):
    """Spec §6 'Leave management' workflow: staff submits -> Staff Admin
    reviews -> approves/rejects -> system records decision -> user
    receives notification (wired via services.decide_leave_request()
    calling smsApp.services.send_notification() from Phase 15)."""

    class LeaveType(models.TextChoices):
        ANNUAL = "ANNUAL", "Annual Leave"
        SICK = "SICK", "Sick Leave"
        MATERNITY = "MATERNITY", "Maternity Leave"
        PATERNITY = "PATERNITY", "Paternity Leave"
        COMPASSIONATE = "COMPASSIONATE", "Compassionate Leave"
        UNPAID = "UNPAID", "Unpaid Leave"
        OTHER = "OTHER", "Other"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    staff = models.ForeignKey(
        Staff, on_delete=models.CASCADE, related_name="leave_requests"
    )
    leave_type = models.CharField(max_length=15, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    reviewed_by = models.ForeignKey(
        "smsApp.User", on_delete=models.SET_NULL, related_name="leave_requests_reviewed",
        blank=True, null=True,
    )
    reviewed_at = models.DateTimeField(blank=True, null=True)
    decision_notes = models.CharField(max_length=255, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "leave_requests"
        ordering = ["-requested_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="leave_end_date_after_start",
            )
        ]

    def __str__(self) -> str:
        return f"{self.staff} - {self.get_leave_type_display()} ({self.status})"
