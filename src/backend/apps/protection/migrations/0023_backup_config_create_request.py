from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("protection", "0022_snapshot_storage_efficiency"),
    ]

    operations = [
        migrations.CreateModel(
            name="BackupConfigCreateRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("organization_id", models.BigIntegerField(db_index=True)),
                ("idempotency_key", models.CharField(max_length=128)),
                ("request_digest", models.CharField(max_length=64)),
                ("response_status", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("response_payload", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "backup_config",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="create_requests",
                        to="protection.backupconfig",
                    ),
                ),
            ],
            options={
                "db_table": "protection_backup_config_create_request",
                "indexes": [
                    models.Index(
                        fields=["organization_id", "created_at"],
                        name="prot_bcfg_create_org_cr_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization_id", "idempotency_key"),
                        name="uniq_prot_bcfg_create_org_key",
                    ),
                ],
            },
        ),
    ]
