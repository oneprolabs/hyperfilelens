from __future__ import annotations

from collections.abc import Iterable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    SnapshotUsageLease,
)


class SnapshotUsageConflict(ValidationError):
    """Raised when a snapshot is already protected or cannot be deleted."""


def _consumer_id(value: int | str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError("Snapshot usage consumer ID is required.")
    return normalized


def acquire_snapshot_usage(
    *,
    organization_id: int,
    snapshot_id: int,
    consumer_type: str,
    consumer_id: int | str,
) -> SnapshotUsageLease:
    """Protect an available snapshot; safe to call repeatedly."""
    normalized_consumer_type = str(consumer_type).strip()
    if normalized_consumer_type not in SnapshotUsageLease.ConsumerType.values:
        raise ValueError(
            f"Unsupported snapshot usage consumer type: {normalized_consumer_type}"
        )
    snapshot_identity = (
        BackupSourceSnapshot.objects.filter(
            organization_id=organization_id,
            pk=int(snapshot_id),
        )
        .values("source_type", "source_ref_id")
        .first()
    )
    if snapshot_identity is None:
        raise ValidationError({"source_snapshot_id": "Snapshot not found."})
    source_type = str(snapshot_identity["source_type"])
    if source_type == "host":
        source_type = "agent"
    with transaction.atomic():
        from apps.source.services.internal.source_operation_fence import (
            active_source_control_task,
            lock_source_identity,
        )

        # Match Source Reset/Unregister's source -> snapshot lock order. This
        # makes the control-task check and lease creation one admission fence:
        # cleanup cannot start after the check but before the lease commits.
        lock_source_identity(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=int(snapshot_identity["source_ref_id"]),
        )
        control_task = active_source_control_task(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=int(snapshot_identity["source_ref_id"]),
        )
        if control_task is not None:
            raise SnapshotUsageConflict(
                {
                    "source_snapshot_id": (
                        "The backup source is being reset or deregistered."
                    ),
                    "task_uuid": str(control_task.task_uuid),
                }
            )
        if source_type == "agent":
            from apps.node.models import Node, NodeTask

            node_removal_active = Node.objects.filter(
                organization_id=organization_id,
                id=int(snapshot_identity["source_ref_id"]),
                status__in={Node.Status.REMOVING, Node.Status.CLEANING_UP},
            ).exists() or NodeTask.objects.filter(
                organization_id=organization_id,
                node_id=int(snapshot_identity["source_ref_id"]),
                kind="agent.uninstall",
                status__in={NodeTask.Status.PENDING, NodeTask.Status.RUNNING},
            ).exists()
            if node_removal_active:
                raise SnapshotUsageConflict(
                    {
                        "source_snapshot_id": (
                            "The backup source Agent is being removed."
                        )
                    }
                )
        snapshot = (
            BackupSourceSnapshot.objects.select_for_update()
            .filter(organization_id=organization_id, pk=int(snapshot_id))
            .first()
        )
        if snapshot is None:
            raise ValidationError({"source_snapshot_id": "Snapshot not found."})
        if snapshot.status not in {
            BackupSourceSnapshot.Status.AVAILABLE,
            BackupSourceSnapshot.Status.PARTIAL,
        }:
            raise SnapshotUsageConflict(
                {"source_snapshot_id": "Snapshot is no longer available."}
            )
        if BackupConfig.objects.filter(
            organization_id=organization_id,
            id=snapshot.backup_config_id,
            status=BackupConfig.Status.RESETTING,
        ).exists():
            raise SnapshotUsageConflict(
                {"source_snapshot_id": "Snapshot configuration is being reset."}
            )
        lease, _ = SnapshotUsageLease.objects.get_or_create(
            organization_id=organization_id,
            snapshot_id=int(snapshot_id),
            consumer_type=normalized_consumer_type,
            consumer_id=_consumer_id(consumer_id),
        )
        return lease


def release_snapshot_usage(
    *,
    snapshot_id: int,
    consumer_type: str,
    consumer_id: int | str,
) -> int:
    """Release one usage lease; repeated release is idempotent."""
    return SnapshotUsageLease.objects.filter(
        snapshot_id=int(snapshot_id),
        consumer_type=str(consumer_type),
        consumer_id=_consumer_id(consumer_id),
    ).delete()[0]


def snapshot_is_protected(*, snapshot_id: int) -> bool:
    return SnapshotUsageLease.objects.filter(snapshot_id=int(snapshot_id)).exists()


def protected_snapshot_ids(snapshot_ids: Iterable[int] | None = None) -> set[int]:
    queryset = SnapshotUsageLease.objects.all()
    if snapshot_ids is not None:
        ids = {int(value) for value in snapshot_ids}
        if not ids:
            return set()
        queryset = queryset.filter(snapshot_id__in=ids)
    return {int(value) for value in queryset.values_list("snapshot_id", flat=True)}


def release_restore_usage(*, restore_record_id: int, snapshot_id: int) -> int:
    return release_snapshot_usage(
        snapshot_id=snapshot_id,
        consumer_type=SnapshotUsageLease.ConsumerType.RESTORE,
        consumer_id=restore_record_id,
    )


def release_chat_usage(*, session_link_id: int, snapshot_id: int) -> int:
    return release_snapshot_usage(
        snapshot_id=snapshot_id,
        consumer_type=SnapshotUsageLease.ConsumerType.CHAT,
        consumer_id=session_link_id,
    )


def reconcile_snapshot_usage_leases(*, limit: int = 500) -> dict[str, int]:
    """Release orphaned or terminal leases after interrupted callbacks."""
    from apps.lens_bridge.models import LensSessionLink
    from apps.node.models import NodeTask
    from apps.restore.models import RestoreRecord
    from apps.task.models import Task

    released = 0
    retained = 0
    retained_lease_ids: list[int] = []
    leases = list(
        SnapshotUsageLease.objects.order_by(
            F("last_reconciled_at").asc(nulls_first=True),
            "id",
        )[: max(1, int(limit))]
    )
    for lease in leases:
        try:
            consumer_pk = int(lease.consumer_id)
        except (TypeError, ValueError):
            retained += 1
            retained_lease_ids.append(lease.id)
            continue
        if lease.consumer_type == SnapshotUsageLease.ConsumerType.RESTORE:
            record = RestoreRecord.objects.filter(pk=consumer_pk).first()
            if record is None:
                orphan_remote_work_active = NodeTask.objects.filter(
                    status__in={NodeTask.Status.PENDING, NodeTask.Status.RUNNING},
                ).filter(
                    Q(payload__restore_record_id=consumer_pk)
                    | Q(payload__restore_record_id=str(consumer_pk))
                ).exists()
                if orphan_remote_work_active:
                    retained += 1
                    retained_lease_ids.append(lease.id)
                    continue
                lease.delete()
                released += 1
                continue
            remote_work_active = NodeTask.objects.filter(
                Q(organization_id=record.organization_id)
                | Q(organization_id=record.target_execution_organization_id),
                correlation_id=str(record.task_uuid),
                status__in={NodeTask.Status.PENDING, NodeTask.Status.RUNNING},
            ).exists()
            if remote_work_active:
                retained += 1
                retained_lease_ids.append(lease.id)
                continue
            task_status = (
                Task.objects.filter(pk=record.task_id)
                .values_list("status", flat=True)
                .first()
            )
            if task_status is None:
                lease.delete()
                released += 1
                continue
            if task_status not in {
                Task.Status.SUCCESS,
                Task.Status.FAILED,
                Task.Status.CANCELLED,
                Task.Status.TIMEOUT,
            }:
                retained += 1
                retained_lease_ids.append(lease.id)
                continue
            lease.delete()
            released += 1
            continue

        if lease.consumer_type == SnapshotUsageLease.ConsumerType.CHAT:
            with transaction.atomic():
                session = (
                    LensSessionLink.all_objects.select_for_update()
                    .filter(pk=consumer_pk)
                    .first()
                )
                if session is None:
                    lease.delete()
                    released += 1
                    continue
                if session.lifecycle_status in {
                    LensSessionLink.LifecycleStatus.READY,
                    LensSessionLink.LifecycleStatus.DELETED,
                } or (
                    session.lifecycle_status == LensSessionLink.LifecycleStatus.DELETING
                    and session.cleanup_status == LensSessionLink.CleanupStatus.COMPLETE
                ) or (
                    session.lifecycle_status == LensSessionLink.LifecycleStatus.FAILED
                    and session.provision_next_retry_at is None
                    and session.cleanup_status
                    in {
                        LensSessionLink.CleanupStatus.NONE,
                        LensSessionLink.CleanupStatus.COMPLETE,
                    }
                ):
                    lease.delete()
                    released += 1
                    continue
                retained += 1
                retained_lease_ids.append(lease.id)
            continue

        # Unknown legacy consumers remain protected until an operator or a
        # compatible release can classify them safely.
        retained += 1
        retained_lease_ids.append(lease.id)
    if retained_lease_ids:
        SnapshotUsageLease.objects.filter(id__in=retained_lease_ids).update(
            last_reconciled_at=timezone.now()
        )
    return {"checked": len(leases), "released": released, "retained": retained}
