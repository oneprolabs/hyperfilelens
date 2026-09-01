from django.db import migrations
from django.db.models import Q


def backfill_snapshot_usage_leases(apps, schema_editor):
    lease_model = apps.get_model("protection", "SnapshotUsageLease")
    snapshot_model = apps.get_model("protection", "BackupSourceSnapshot")
    restore_model = apps.get_model("restore", "RestoreRecord")
    task_model = apps.get_model("task", "Task")
    session_model = apps.get_model("lens_bridge", "LensSessionLink")

    active_task_statuses = {"pending", "waiting", "blocked", "running"}
    active_restore_task_ids = set(
        task_model.objects.filter(
            task_type__in={"restore", "insight_workspace_restore"},
            status__in=active_task_statuses,
        ).values_list("id", flat=True)
    )
    restore_rows = list(
        restore_model.objects.filter(
            task_id__in=active_restore_task_ids,
            source_snapshot_id__isnull=False,
        ).values("id", "organization_id", "source_snapshot_id")
    )
    active_session_rows = list(
        session_model.objects.filter(
            backup_source_snapshot_id__isnull=False,
        )
        .filter(
            Q(lifecycle_status__in={"provisioning", "deleting"})
            | Q(
                lifecycle_status="failed",
                cleanup_status__in={"pending", "running", "blocked"},
            )
        )
        .values("id", "organization_id", "backup_source_snapshot_id")
    )
    referenced_snapshot_ids = {
        row["source_snapshot_id"] for row in restore_rows
    } | {row["backup_source_snapshot_id"] for row in active_session_rows}
    # Backfill every snapshot that has not reached the completed deleted state.
    # A legacy active Chat/Restore may already have driven the snapshot into an
    # intermediate deleting/failed state; omitting that lease would let an old
    # delete task race the still-running consumer during migration.
    valid_snapshot_keys = set(
        snapshot_model.objects.filter(
            id__in=referenced_snapshot_ids,
            deleted_at__isnull=True,
        )
        .exclude(status="deleted")
        .values_list("organization_id", "id")
    )
    lease_rows = [
        lease_model(
            organization_id=row["organization_id"],
            snapshot_id=row["source_snapshot_id"],
            consumer_type="restore",
            consumer_id=str(row["id"]),
        )
        for row in restore_rows
        if (
            row["organization_id"],
            row["source_snapshot_id"],
        )
        in valid_snapshot_keys
    ]
    if lease_rows:
        lease_model.objects.bulk_create(
            lease_rows,
            ignore_conflicts=True,
        )

    session_leases = [
        lease_model(
            organization_id=row["organization_id"],
            snapshot_id=row["backup_source_snapshot_id"],
            consumer_type="chat",
            consumer_id=str(row["id"]),
        )
        for row in active_session_rows
        if (
            row["organization_id"],
            row["backup_source_snapshot_id"],
        )
        in valid_snapshot_keys
    ]
    if session_leases:
        lease_model.objects.bulk_create(session_leases, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        ("protection", "0024_snapshot_usage_lease"),
        ("restore", "0008_user_restore_idempotency"),
        ("task", "0015_backup_config_provision_task_type"),
        ("lens_bridge", "0040_session_analysis_type"),
    ]

    operations = [migrations.RunPython(backfill_snapshot_usage_leases, migrations.RunPython.noop)]
