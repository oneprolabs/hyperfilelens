import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("iam", "0014_drop_membership_role_column"),
        ("monitor", "0005_repository_usage_metric"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationalEvent",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("event_type", models.CharField(db_index=True, max_length=120)),
                (
                    "category",
                    models.CharField(
                        choices=[
                            ("protection", "Protection"),
                            ("infrastructure", "Infrastructure"),
                            ("system", "System"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("information", "Information"),
                            ("warning", "Warning"),
                            ("critical", "Critical"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("details", models.TextField(blank=True, default="")),
                (
                    "occurred_at",
                    models.DateTimeField(
                        default=django.utils.timezone.now, db_index=True
                    ),
                ),
                (
                    "resource_type",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=64
                    ),
                ),
                (
                    "resource_id",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                (
                    "resource_name",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("source", models.CharField(blank=True, default="", max_length=120)),
                (
                    "target_path",
                    models.CharField(blank=True, default="", max_length=1000),
                ),
                (
                    "correlation_id",
                    models.CharField(
                        blank=True, db_index=True, default="", max_length=100
                    ),
                ),
                ("dedup_key", models.CharField(blank=True, default="", max_length=255)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="operational_events",
                        to="iam.organization",
                    ),
                ),
            ],
            options={
                "db_table": "monitor_operational_events",
                "ordering": ["-occurred_at", "-id"],
            },
        ),
        migrations.AddIndex(
            model_name="operationalevent",
            index=models.Index(
                fields=["organization", "-occurred_at"],
                name="mon_event_org_at_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="operationalevent",
            index=models.Index(
                fields=["organization", "category", "-occurred_at"],
                name="mon_event_org_cat_at_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="operationalevent",
            index=models.Index(
                fields=["organization", "severity", "-occurred_at"],
                name="mon_event_org_sev_at_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="operationalevent",
            constraint=models.UniqueConstraint(
                fields=("organization", "dedup_key"),
                condition=~Q(dedup_key=""),
                name="mon_event_org_dedup_uniq",
            ),
        ),
    ]
