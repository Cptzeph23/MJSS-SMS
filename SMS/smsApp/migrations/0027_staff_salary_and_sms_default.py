from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("smsApp", "0026_deactivate_campus_program")]

    operations = [
        migrations.AddField(
            model_name="staff",
            name="salary",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Gross salary or agreed monthly salary amount.",
                max_digits=12,
            ),
        ),
        migrations.AlterField(
            model_name="notificationpreference",
            name="sms_enabled",
            field=models.BooleanField(default=True),
        ),
    ]
