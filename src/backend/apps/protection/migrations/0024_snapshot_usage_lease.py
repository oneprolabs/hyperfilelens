import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("protection", "0023_backup_config_create_request"),
    ]

    operations = [
        migrations.CreateModel(
            name="SnapshotUsageLease",
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
                ("organization_id", models.BigIntegerField(db_index=True)),
                (
                    "snapshot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="usage_leases",
                        to="protection.backupsourcesnapshot",
                    ),
                ),
                (
                    "consumer_type",
                    models.CharField(
                        choices=[
                            ("restore", "Restore"),
                            ("chat", "Chat preparation"),
                        ],
                        max_length=16,
                    ),
                ),
                ("consumer_id", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                "db_table": "protection_snapshot_usage_lease",
                "indexes": [
                    models.Index(
                        fields=["organization_id", "snapshot_id"],
                        name="prot_snap_usage_org_snap_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=["snapshot_id", "consumer_type", "consumer_id"],
                        name="uniq_prot_snap_usage_consumer",
                    ),
                ],
            },
        ),
    ]
