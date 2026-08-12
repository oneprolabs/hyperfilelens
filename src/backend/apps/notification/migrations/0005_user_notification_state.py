from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """Create per-user notification inbox state."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("iam", "0014_drop_membership_role_column"),
        ("notification", "0004_rename_notification_del_organ_5a47c0_idx_notif_del_org_status_cr_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserNotification",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("event_type", models.CharField(db_index=True, max_length=120)),
                ("source_type", models.CharField(max_length=80)),
                ("source_id", models.CharField(max_length=100)),
                ("title", models.CharField(max_length=255)),
                ("summary", models.TextField(blank=True, default="")),
                ("severity", models.CharField(blank=True, default="info", max_length=50)),
                ("target_url", models.CharField(blank=True, default="", max_length=500)),
                ("read_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_notifications",
                        to="iam.organization",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="in_app_notifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "notification_user", "ordering": ["-updated_at", "-id"]},
        ),
        migrations.AddConstraint(
            model_name="usernotification",
            constraint=models.UniqueConstraint(
                fields=("user", "organization", "event_type", "source_type", "source_id"),
                name="uniq_notification_user_event",
            ),
        ),
        migrations.AddIndex(
            model_name="usernotification",
            index=models.Index(
                fields=["user", "organization", "read_at", "updated_at"],
                name="notif_user_org_read_idx",
            ),
        ),
    ]
