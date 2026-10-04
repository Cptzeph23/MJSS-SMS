import csv
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.crypto import get_random_string

from smsApp.models import Class, School, Student, User


class Command(BaseCommand):
    help = "Import or update longitudinal student records from a CSV file."
    required_columns = {"username", "admission_number", "admission_date", "gender"}

    def add_arguments(self, parser):
        parser.add_argument("csv_file", type=Path)
        parser.add_argument("--school", required=True, help="School code")
        parser.add_argument("--default-password", default=None)

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = options["csv_file"]
        school = School.objects.filter(code=options["school"], is_active=True).first()
        if school is None:
            raise CommandError("Active school not found for the supplied code.")
        try:
            with csv_path.open(newline="", encoding="utf-8-sig") as source:
                reader = csv.DictReader(source)
                missing = self.required_columns - set(reader.fieldnames or [])
                if missing:
                    raise CommandError("Missing required columns: " + ", ".join(sorted(missing)))
                created = updated = 0
                temporary_credentials = []
                for line_number, row in enumerate(reader, start=2):
                    admission_number = (row.get("admission_number") or "").strip()
                    username = (row.get("username") or "").strip()
                    if not admission_number or not username:
                        raise CommandError(f"Line {line_number}: username and admission_number are required.")
                    try:
                        admission_date = date.fromisoformat((row["admission_date"] or "").strip())
                    except ValueError as exc:
                        raise CommandError(f"Line {line_number}: admission_date must be YYYY-MM-DD.") from exc
                    gender_aliases = {"f": Student.Gender.FEMALE, "female": Student.Gender.FEMALE,
                                      "m": Student.Gender.MALE, "male": Student.Gender.MALE,
                                      "o": Student.Gender.OTHER, "other": Student.Gender.OTHER}
                    gender = gender_aliases.get((row.get("gender") or "").strip().casefold())
                    if gender is None:
                        raise CommandError(f"Line {line_number}: gender must be Female (F), Male (M), or Other (O).")
                    student = Student.objects.filter(
                        school=school, admission_number=admission_number
                    ).select_related("user").first()
                    if student is None:
                        user = User.objects.filter(username=username).first()
                        if user is not None and hasattr(user, "student_profile"):
                            raise CommandError(f"Line {line_number}: username is already another student.")
                        if user is None:
                            temporary_password = options["default_password"] or get_random_string(16)
                            user = User.objects.create_user(
                                username=username,
                                password=temporary_password,
                                role=User.Role.STUDENT,
                                must_change_password=True,
                            )
                            temporary_credentials.append(f"{username}: {temporary_password}")
                        elif user.role != User.Role.STUDENT:
                            raise CommandError(f"Line {line_number}: username belongs to a non-student user.")
                        student = Student.objects.create(
                            user=user, school=school, admission_number=admission_number,
                            admission_date=admission_date, gender=gender,
                        )
                        created += 1
                    else:
                        user = student.user
                        updated += 1
                    user.first_name = (row.get("first_name") or "").strip()
                    user.last_name = (row.get("last_name") or "").strip()
                    user.email = (row.get("email") or "").strip()
                    user.save(update_fields=["first_name", "last_name", "email", "updated_at"])
                    updates = {"admission_date": admission_date, "gender": gender}
                    if row.get("status"):
                        updates["status"] = row["status"].strip()
                    class_name = (row.get("class_name") or "").strip()
                    if class_name:
                        class_group = Class.objects.filter(school=school, name=class_name).first()
                        if class_group is None:
                            raise CommandError(f"Line {line_number}: class_name not found in school.")
                        updates["current_class"] = class_group
                    Student.objects.filter(pk=student.pk).update(**updates)
        except FileNotFoundError as exc:
            raise CommandError(f"CSV file not found: {csv_path}") from exc
        self.stdout.write(self.style.SUCCESS(f"Imported students: {created} created, {updated} updated."))
        if temporary_credentials:
            self.stdout.write("Temporary student credentials (change required at first sign-in):")
            for credential in temporary_credentials:
                self.stdout.write(credential)
