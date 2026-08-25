from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("restore", "0006_restore_item_terminal_projection")]

    operations = [
        migrations.CreateModel(
            name="DirectNASMount",
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
                ("execution_organization_id", models.BigIntegerField(db_index=True)),
                ("requesting_organization_id", models.BigIntegerField(db_index=True)),
                ("repository_id", models.BigIntegerField(db_index=True)),
                ("reader_node_id", models.BigIntegerField(db_index=True)),
                ("mount_point", models.CharField(max_length=1000)),
                ("mount_key", models.CharField(max_length=64)),
                (
                    "cleanup_node_task_id",
                    models.UUIDField(blank=True, db_index=True, null=True),
                ),
                (
                    "cleanup_after",
                    models.DateTimeField(blank=True, db_index=True, null=True),
                ),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"db_table": "restore_direct_nas_mount"},
        ),
        migrations.CreateModel(
            name="DirectNASMountLease",
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
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("released", "Released"),
                            ("cleanup_pending", "Cleanup pending"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=24,
                    ),
                ),
                (
                    "cleanup_node_task_id",
                    models.UUIDField(blank=True, db_index=True, null=True),
                ),
                ("released_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "mount",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="leases",
                        to="restore.directnasmount",
                    ),
                ),
                (
                    "restore_record",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="direct_nas_mount_leases",
                        to="restore.restorerecord",
                    ),
                ),
            ],
            options={"db_table": "restore_direct_nas_mount_lease"},
        ),
        migrations.AddConstraint(
            model_name="directnasmount",
            constraint=models.UniqueConstraint(
                fields=(
                    "execution_organization_id",
                    "repository_id",
                    "reader_node_id",
                    "mount_key",
                ),
                name="uniq_restore_direct_nas_mount",
            ),
        ),
        migrations.AddIndex(
            model_name="directnasmount",
            index=models.Index(
                fields=["reader_node_id", "repository_id"],
                name="rst_nas_mount_node_repo_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="directnasmountlease",
            constraint=models.UniqueConstraint(
                fields=("restore_record", "mount"),
                name="uniq_restore_direct_nas_lease",
            ),
        ),
        migrations.AddIndex(
            model_name="directnasmountlease",
            index=models.Index(
                fields=["mount", "status"],
                name="rst_nas_lease_mount_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="directnasmountlease",
            index=models.Index(
                fields=["restore_record", "status"],
                name="rst_nas_lease_rec_status_idx",
            ),
        ),
    ]
