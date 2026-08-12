import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("lens_bridge", "0029_usage_ledger_reconciliation"),
    ]

    operations = [
        migrations.CreateModel(
            name="LensRunSubmission",
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(db_index=True, default=False)),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("idempotency_key", models.CharField(max_length=128)),
                ("question", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("bound", "Bound"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("sl_run_uuid", models.UUIDField(blank=True, null=True, unique=True)),
                ("run_status", models.CharField(blank=True, default="", max_length=24)),
                ("last_error", models.TextField(blank=True, default="")),
                ("recovery_attempts", models.PositiveIntegerField(default=0)),
                (
                    "recovery_claim_token",
                    models.UUIDField(blank=True, null=True, unique=True),
                ),
                (
                    "recovery_claimed_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "recovery_next_at",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                (
                    "hfl_user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lens_run_submissions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="iam.organization",
                    ),
                ),
                (
                    "session_link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="run_submissions",
                        to="lens_bridge.lenssessionlink",
                    ),
                ),
            ],
            options={
                "db_table": "lens_bridge_run_submission",
                "ordering": ["created_at", "id"],
                "indexes": [
                    models.Index(
                        fields=["status", "recovery_next_at"],
                        name="lens_brunsub_st_retry_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("session_link", "idempotency_key"),
                        name="uniq_lens_runsub_session_key",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(is_deleted=False, status="pending"),
                        fields=("session_link",),
                        name="uniq_lens_runsub_pending_session",
                    ),
                ],
            },
        ),
    ]
