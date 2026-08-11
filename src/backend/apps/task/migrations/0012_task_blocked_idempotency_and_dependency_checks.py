from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("task", "0011_task_waiting_and_dependency")]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("waiting", "Waiting"),
                    ("blocked", "Blocked"),
                    ("running", "Running"),
                    ("success", "Success"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                    ("timeout", "Timeout"),
                ],
                db_index=True,
                default="pending",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="group_uuid",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="task",
            name="idempotency_key",
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddConstraint(
            model_name="task",
            constraint=models.UniqueConstraint(
                condition=models.Q(idempotency_key__isnull=False),
                fields=("organization_id", "idempotency_key"),
                name="task_org_idempotency_uniq",
            ),
        ),
        migrations.AddField(
            model_name="taskdependency",
            name="last_checked_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="taskdependency",
            name="next_check_at",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]
