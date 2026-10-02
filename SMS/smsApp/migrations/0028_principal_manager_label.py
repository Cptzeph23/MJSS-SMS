from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("smsApp", "0027_staff_salary_and_sms_default")]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("SUPER_ADMIN", "Super Admin"),
                    ("PRINCIPAL_DIRECTOR", "Principal/Manager"),
                    ("DEPUTY_PRINCIPAL", "Deputy Principal"),
                    ("STAFF_ADMIN", "Staff Admin"),
                    ("ACADEMIC_ADMIN", "Academic Admin"),
                    ("FINANCE_ADMIN", "Finance Admin"),
                    ("TEACHER", "Teacher/Lecturer"),
                    ("EXAM_OFFICER", "Examination Officer"),
                    ("CLASS_TEACHER", "Class Teacher"),
                    ("DEPARTMENT_HEAD", "Department Head"),
                    ("ACCOUNTANT", "Accountant/Finance Officer"),
                    ("LIBRARIAN", "Librarian"),
                    ("STUDENT", "Student"),
                    ("PARENT", "Parent/Guardian"),
                ],
                db_index=True,
                default="STUDENT",
                help_text="Coarse role for UI/dashboard routing. Authorization decisions must still check Django permissions.",
                max_length=20,
            ),
        ),
    ]
