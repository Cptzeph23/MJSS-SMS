from django.db import migrations


def deactivate_campus_program(apps, schema_editor):
    Campus = apps.get_model("smsApp", "Campus")
    Program = apps.get_model("smsApp", "Program")
    Class = apps.get_model("smsApp", "Class")
    Department = apps.get_model("smsApp", "Department")
    Student = apps.get_model("smsApp", "Student")
    Class.objects.update(campus=None, program=None)
    Department.objects.update(campus=None)
    Student.objects.update(program=None)
    Campus.objects.update(is_active=False)
    Program.objects.update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("smsApp", "0025_alter_class_program")]
    operations = [migrations.RunPython(deactivate_campus_program, migrations.RunPython.noop)]
