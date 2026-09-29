

import django.contrib.auth.models
import django.contrib.auth.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='Campus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('code', models.CharField(max_length=20)),
                ('address', models.TextField(blank=True)),
                ('is_main', models.BooleanField(default=False, help_text="Marks the school's primary/head campus.")),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'campuses',
                'ordering': ['school', 'name'],
            },
        ),
        migrations.CreateModel(
            name='School',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('code', models.CharField(max_length=20, unique=True)),
                ('motto', models.CharField(blank=True, max_length=255)),
                ('logo', models.ImageField(blank=True, null=True, upload_to='school/logos/')),
                ('address', models.TextField(blank=True)),
                ('phone_number', models.CharField(blank=True, max_length=20)),
                ('email', models.EmailField(blank=True, max_length=254)),
                ('established_date', models.DateField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'db_table': 'schools',
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='User',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('password', models.CharField(max_length=128, verbose_name='password')),
                ('last_login', models.DateTimeField(blank=True, null=True, verbose_name='last login')),
                ('is_superuser', models.BooleanField(default=False, help_text='Designates that this user has all permissions without explicitly assigning them.', verbose_name='superuser status')),
                ('username', models.CharField(error_messages={'unique': 'A user with that username already exists.'}, help_text='Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.', max_length=150, unique=True, validators=[django.contrib.auth.validators.UnicodeUsernameValidator()], verbose_name='username')),
                ('first_name', models.CharField(blank=True, max_length=150, verbose_name='first name')),
                ('last_name', models.CharField(blank=True, max_length=150, verbose_name='last name')),
                ('email', models.EmailField(blank=True, max_length=254, verbose_name='email address')),
                ('is_staff', models.BooleanField(default=False, help_text='Designates whether the user can log into this admin site.', verbose_name='staff status')),
                ('is_active', models.BooleanField(default=True, help_text='Designates whether this user should be treated as active. Unselect this instead of deleting accounts.', verbose_name='active')),
                ('date_joined', models.DateTimeField(default=django.utils.timezone.now, verbose_name='date joined')),
                ('role', models.CharField(choices=[('SUPER_ADMIN', 'Super Admin'), ('STAFF_ADMIN', 'Staff Admin'), ('ACADEMIC_ADMIN', 'Academic Admin'), ('FINANCE_ADMIN', 'Finance Admin'), ('TEACHER', 'Teacher/Lecturer'), ('EXAM_OFFICER', 'Examination Officer'), ('CLASS_TEACHER', 'Class Teacher'), ('DEPARTMENT_HEAD', 'Department Head'), ('ACCOUNTANT', 'Accountant/Finance Officer'), ('LIBRARIAN', 'Librarian'), ('STUDENT', 'Student'), ('PARENT', 'Parent/Guardian')], db_index=True, default='STUDENT', help_text='Coarse role for UI/dashboard routing. Authorization decisions must still check Django permissions.', max_length=20)),
                ('phone_number', models.CharField(blank=True, max_length=20)),
                ('is_locked', models.BooleanField(default=False, help_text="Account lock distinct from is_active — used for Super Admin 'lock/unlock account' action (§5).")),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('groups', models.ManyToManyField(blank=True, help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.', related_name='user_set', related_query_name='user', to='auth.group', verbose_name='groups')),
                ('user_permissions', models.ManyToManyField(blank=True, help_text='Specific permissions for this user.', related_name='user_set', related_query_name='user', to='auth.permission', verbose_name='user permissions')),
            ],
            options={
                'verbose_name': 'User',
                'verbose_name_plural': 'Users',
                'db_table': 'users',
                'ordering': ['-created_at'],
            },
            managers=[
                ('objects', django.contrib.auth.models.UserManager()),
            ],
        ),
        migrations.CreateModel(
            name='Department',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('code', models.CharField(max_length=20)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('campus', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='departments', to='smsApp.campus')),
                ('head', models.ForeignKey(blank=True, limit_choices_to={'role__in': ['DEPARTMENT_HEAD', 'TEACHER', 'STAFF_ADMIN']}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='departments_headed', to=settings.AUTH_USER_MODEL)),
                ('school', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='departments', to='smsApp.school')),
            ],
            options={
                'db_table': 'departments',
                'ordering': ['school', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Program',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('code', models.CharField(max_length=20)),
                ('program_type', models.CharField(choices=[('SCHOOL', 'School-style (Grade/Class based)'), ('UNIVERSITY', 'University-style (Credit/Course based)')], default='SCHOOL', max_length=15)),
                ('description', models.TextField(blank=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='programs', to='smsApp.department')),
                ('school', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='programs', to='smsApp.school')),
            ],
            options={
                'db_table': 'programs',
                'ordering': ['school', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Class',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="e.g. 'Grade 10', 'Form 2'", max_length=100)),
                ('level_order', models.PositiveSmallIntegerField(default=0, help_text='Sort order for progression, e.g. Grade 1=1, Grade 2=2.')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('campus', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='classes', to='smsApp.campus')),
                ('class_teacher', models.ForeignKey(blank=True, limit_choices_to={'role__in': ['CLASS_TEACHER', 'TEACHER']}, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='classes_led', to=settings.AUTH_USER_MODEL)),
                ('department', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='classes', to='smsApp.department')),
                ('program', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='classes', to='smsApp.program')),
                ('school', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='classes', to='smsApp.school')),
            ],
            options={
                'verbose_name_plural': 'Classes',
                'db_table': 'classes',
                'ordering': ['school', 'program', 'level_order', 'name'],
            },
        ),
        migrations.AddField(
            model_name='campus',
            name='school',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='campuses', to='smsApp.school'),
        ),
        migrations.CreateModel(
            name='AcademicYear',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="e.g. '2026/2027'", max_length=20)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('is_current', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('school', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='academic_years', to='smsApp.school')),
            ],
            options={
                'db_table': 'academic_years',
                'ordering': ['-start_date'],
            },
        ),
        migrations.CreateModel(
            name='Stream',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50)),
                ('capacity', models.PositiveIntegerField(default=0, help_text='0 = unlimited')),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('class_group', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='streams', to='smsApp.class')),
            ],
            options={
                'db_table': 'streams',
                'ordering': ['class_group', 'name'],
            },
        ),
        migrations.CreateModel(
            name='Term',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text="e.g. 'Term 1', 'Semester 1'", max_length=50)),
                ('term_number', models.PositiveSmallIntegerField()),
                ('start_date', models.DateField()),
                ('end_date', models.DateField()),
                ('is_current', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('academic_year', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='terms', to='smsApp.academicyear')),
            ],
            options={
                'db_table': 'terms',
                'ordering': ['academic_year', 'term_number'],
            },
        ),
        migrations.AddConstraint(
            model_name='program',
            constraint=models.UniqueConstraint(fields=('school', 'code'), name='uniq_program_code_per_school'),
        ),
        migrations.AddConstraint(
            model_name='department',
            constraint=models.UniqueConstraint(fields=('school', 'code'), name='uniq_department_code_per_school'),
        ),
        migrations.AddConstraint(
            model_name='class',
            constraint=models.UniqueConstraint(fields=('program', 'name'), name='uniq_class_name_per_program'),
        ),
        migrations.AddConstraint(
            model_name='campus',
            constraint=models.UniqueConstraint(fields=('school', 'code'), name='uniq_campus_code_per_school'),
        ),
        migrations.AddConstraint(
            model_name='academicyear',
            constraint=models.UniqueConstraint(fields=('school', 'name'), name='uniq_academic_year_per_school'),
        ),
        migrations.AddConstraint(
            model_name='academicyear',
            constraint=models.CheckConstraint(condition=models.Q(('end_date__gt', models.F('start_date'))), name='academic_year_end_after_start'),
        ),
        migrations.AddConstraint(
            model_name='stream',
            constraint=models.UniqueConstraint(fields=('class_group', 'name'), name='uniq_stream_name_per_class'),
        ),
        migrations.AddConstraint(
            model_name='term',
            constraint=models.UniqueConstraint(fields=('academic_year', 'term_number'), name='uniq_term_number_per_year'),
        ),
        migrations.AddConstraint(
            model_name='term',
            constraint=models.CheckConstraint(condition=models.Q(('end_date__gt', models.F('start_date'))), name='term_end_after_start'),
        ),
    ]