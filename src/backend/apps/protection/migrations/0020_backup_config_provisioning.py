from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("protection", "0019_normalize_policy_start_seconds"),
    ]

    operations = [
        migrations.AddField(
            model_name="backupconfig",
            name="provisioning_task_uuid",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="backupconfig",
            name="provisioning_error_code",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="backupconfig",
            name="provisioning_error_message",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="backupconfig",
            name="status",
            field=models.CharField(
                choices=[
                    ("provisioning", "Provisioning"),
                    ("active", "Active"),
                    ("provision_failed", "Provision failed"),
                    ("resetting", "Resetting"),
                    ("reset_failed", "Reset failed"),
                ],
                db_index=True,
                default="active",
                max_length=32,
            ),
        ),
    ]
