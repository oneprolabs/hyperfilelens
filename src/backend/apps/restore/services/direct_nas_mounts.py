"""Lifecycle for restores that temporarily mount an unbound NAS."""

from __future__ import annotations

import logging
from datetime import timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from apps.node.models import Node, NodeTask
from apps.node.services.capabilities import (
    NAS_MOUNT_LIFECYCLE_CAPABILITY,
    node_supports_capability,
)
from apps.node.services.interface import run_agent_task_async
from apps.restore import conf
from apps.restore.models import DirectNASMount, DirectNASMountLease, RestoreRecord
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.nas_repository import nas_restore_mount_point
from apps.task.models import Task
from apps.task.services.interface import TERMINAL_STATUSES


logger = logging.getLogger(__name__)
_CLEANUP_CORRELATION = "restore.direct_nas_mount_cleanup"
_UPGRADE_REQUIRED_ERROR = (
    "Upgrade the Data Gateway Agent to enable safe NAS mount cleanup."
)
_TERMINAL_NODE_STATUSES = {
    NodeTask.Status.SUCCESS,
    NodeTask.Status.FAILED,
    NodeTask.Status.TIMEOUT,
    NodeTask.Status.CANCELED,
}


def acquire_for_restore(
    *,
    record: RestoreRecord,
    repository: Repository,
    reader_node_id: int,
    access_mode: str,
    repository_payload: dict[str, Any],
) -> DirectNASMountLease | None:
    """Record one direct NAS restore read; Proxy reads are excluded.

    The aggregate row is the database lock for the physical mount identity.
    A lease belongs to one restore record, while multiple restores on the
    same Agent share the aggregate and therefore share one physical mount.
    """

    mount_point = _direct_mount_point(
        record=record,
        repository=repository,
        reader_node_id=reader_node_id,
        access_mode=access_mode,
        repository_payload=repository_payload,
    )
    if not mount_point:
        return None

    with transaction.atomic():
        mount_key = sha256(mount_point.encode("utf-8")).hexdigest()
        mount, _ = DirectNASMount.objects.get_or_create(
            execution_organization_id=record.target_execution_organization_id,
            repository_id=repository.id,
            reader_node_id=reader_node_id,
            mount_key=mount_key,
            defaults={
                "requesting_organization_id": record.requesting_organization_id,
                "mount_point": mount_point,
            },
        )
        mount = DirectNASMount.objects.select_for_update().get(pk=mount.pk)
        if mount.mount_point != mount_point:
            raise RuntimeError("Direct NAS mount identity collision.")
        mount_update_fields: list[str] = []
        if mount.requesting_organization_id != record.requesting_organization_id:
            mount.requesting_organization_id = record.requesting_organization_id
            mount_update_fields.append("requesting_organization_id")
        # Cancel only a cleanup that has not been dispatched. An in-flight
        # unmount remains authoritative and the Agent's NAS lease serializes
        # it with this new reader, preventing duplicate cleanup commands.
        if mount.cleanup_node_task_id is None and mount.cleanup_after is not None:
            mount.cleanup_after = None
            mount.last_error = ""
            mount_update_fields.extend(["cleanup_after", "last_error"])
        if mount_update_fields:
            mount.save(
                update_fields=list(dict.fromkeys([*mount_update_fields, "updated_at"]))
            )
        lease, _ = DirectNASMountLease.objects.update_or_create(
            restore_record=record,
            mount=mount,
            defaults={
                "organization_id": record.organization_id,
                "status": DirectNASMountLease.Status.ACTIVE,
                "cleanup_node_task_id": None,
                "released_at": None,
                "last_error": "",
            },
        )
    return lease


def requires_lease_for_restore(
    *,
    record: RestoreRecord,
    repository: Repository,
    reader_node_id: int,
    access_mode: str,
    repository_payload: dict[str, Any],
) -> bool:
    """Return whether dispatch must atomically create a direct NAS lease."""

    return bool(
        _direct_mount_point(
            record=record,
            repository=repository,
            reader_node_id=reader_node_id,
            access_mode=access_mode,
            repository_payload=repository_payload,
        )
    )


def _direct_mount_point(
    *,
    record: RestoreRecord,
    repository: Repository,
    reader_node_id: int,
    access_mode: str,
    repository_payload: dict[str, Any],
) -> str:
    if (
        repository.repo_type != Repository.Type.NAS
        or repository.bind_node_type == Repository.BindNodeType.PROXY
        or access_mode != "fallback_node"
        or int(reader_node_id) != int(record.target_execution_node_id)
    ):
        return ""
    nas = repository_payload.get("nas")
    mount_point = str(nas.get("mount_point") if isinstance(nas, dict) else "").strip()
    expected = nas_restore_mount_point(repository, node_id=reader_node_id)
    return mount_point if mount_point == expected else ""


def release_for_record(*, record: RestoreRecord) -> int:
    """Release a record's references and schedule cleanup when unused."""

    now = timezone.now()
    with transaction.atomic():
        lease_rows = list(
            DirectNASMountLease.objects.filter(
                restore_record_id=record.id,
                status=DirectNASMountLease.Status.ACTIVE,
            ).values("id", "mount_id")
        )
        if not lease_rows:
            return 0
        mount_ids = sorted({int(row["mount_id"]) for row in lease_rows})
        mounts = {
            mount.id: mount
            for mount in DirectNASMount.objects.select_for_update()
            .filter(id__in=mount_ids)
            .order_by("id")
        }
        released = DirectNASMountLease.objects.filter(
            id__in=[row["id"] for row in lease_rows],
            status=DirectNASMountLease.Status.ACTIVE,
        ).update(
            status=DirectNASMountLease.Status.RELEASED,
            released_at=now,
            last_error="",
            updated_at=now,
        )
        for mount_id, mount in mounts.items():
            if DirectNASMountLease.objects.filter(
                mount_id=mount_id,
                status=DirectNASMountLease.Status.ACTIVE,
            ).exists():
                continue
            if mount.cleanup_node_task_id is not None:
                continue
            grace_seconds = (
                conf.DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS
                if record.purpose == RestoreRecord.Purpose.LENS_WORKSPACE
                else conf.DIRECT_NAS_USER_DATA_MOUNT_CLEANUP_GRACE_SECONDS
            )
            mount.cleanup_after = now + timedelta(seconds=grace_seconds)
            mount.last_error = ""
            mount.save(
                update_fields=[
                    "cleanup_after",
                    "last_error",
                    "updated_at",
                ]
            )
        if released and any(
            mount.cleanup_after is not None and mount.cleanup_after <= now
            for mount in mounts.values()
        ):
            transaction.on_commit(
                lambda: _dispatch_due_cleanups(limit=max(1, len(mounts)))
            )
    return released


def reconcile(*, limit: int = 200) -> dict[str, int]:
    """Release orphaned leases and converge due unmount commands."""

    batch_size = max(1, int(limit))
    released = _release_terminal_record_leases(limit=batch_size)
    orphaned = _schedule_unreferenced_mounts(limit=batch_size)
    completed, retried = _reconcile_cleanup_tasks(limit=batch_size)
    dispatched = _dispatch_due_cleanups(limit=batch_size)
    return {
        "released": released,
        "orphaned": orphaned,
        "completed": completed,
        "retried": retried,
        "dispatched": dispatched,
    }


def _release_terminal_record_leases(*, limit: int) -> int:
    existing_task_ids = Task.objects.values("id")
    terminal_task_ids = Task.objects.filter(
        status__in=TERMINAL_STATUSES,
    ).values("id")
    record_ids = list(
        DirectNASMountLease.objects.filter(
            status=DirectNASMountLease.Status.ACTIVE,
        )
        .filter(
            Q(restore_record__task_id__in=terminal_task_ids)
            | ~Q(restore_record__task_id__in=existing_task_ids)
        )
        .order_by()
        .values_list("restore_record_id", flat=True)
        .distinct()[:limit]
    )
    released = 0
    for record_id in record_ids:
        record = RestoreRecord.objects.filter(pk=record_id).first()
        if record is not None:
            released += release_for_record(record=record)
    return released


def _schedule_unreferenced_mounts(*, limit: int) -> int:
    """Schedule aggregates whose RestoreRecord leases were removed."""

    lease_exists = DirectNASMountLease.objects.filter(mount_id=OuterRef("pk"))
    mount_ids = list(
        DirectNASMount.objects.annotate(has_lease=Exists(lease_exists))
        .filter(
            has_lease=False,
            cleanup_node_task_id__isnull=True,
            cleanup_after__isnull=True,
        )
        .order_by("updated_at", "id")
        .values_list("id", flat=True)[:limit]
    )
    scheduled = 0
    for mount_id in mount_ids:
        with transaction.atomic():
            mount = (
                DirectNASMount.objects.select_for_update()
                .filter(
                    pk=mount_id,
                    cleanup_node_task_id__isnull=True,
                    cleanup_after__isnull=True,
                )
                .first()
            )
            if (
                mount is None
                or DirectNASMountLease.objects.filter(mount_id=mount_id).exists()
            ):
                continue
            mount.cleanup_after = timezone.now() + timedelta(
                seconds=conf.DIRECT_NAS_MOUNT_CLEANUP_GRACE_SECONDS
            )
            mount.last_error = ""
            mount.save(update_fields=["cleanup_after", "last_error", "updated_at"])
            scheduled += 1
    return scheduled


def _reconcile_cleanup_tasks(*, limit: int) -> tuple[int, int]:
    existing_cleanup_task_ids = NodeTask.objects.values("id")
    terminal_cleanup_task_ids = NodeTask.objects.filter(
        status__in=_TERMINAL_NODE_STATUSES,
    ).values("id")
    pending = list(
        DirectNASMount.objects.filter(cleanup_node_task_id__isnull=False)
        .filter(
            Q(cleanup_node_task_id__in=terminal_cleanup_task_ids)
            | ~Q(cleanup_node_task_id__in=existing_cleanup_task_ids)
        )
        .order_by("updated_at", "id")
        .values_list("id", "cleanup_node_task_id")[:limit]
    )
    completed = 0
    retried = 0
    for mount_id, cleanup_task_id in pending:
        node_task = NodeTask.objects.filter(pk=cleanup_task_id).first()
        if node_task is None:
            retried += _retry_missing_cleanup_task(
                mount_id=int(mount_id),
                cleanup_task_id=cleanup_task_id,
            )
            continue
        if node_task.status not in _TERMINAL_NODE_STATUSES:
            continue
        if node_task.status == NodeTask.Status.SUCCESS:
            completed += _complete_cleanup_task(
                mount_id=int(mount_id),
                cleanup_task_id=cleanup_task_id,
            )
        else:
            retried += _retry_failed_cleanup_task(
                mount_id=int(mount_id),
                cleanup_task_id=cleanup_task_id,
                error=node_task.last_error or "NAS mount cleanup failed.",
            )
    return completed, retried


@transaction.atomic
def _complete_cleanup_task(*, mount_id: int, cleanup_task_id: UUID) -> int:
    """Converge one successful unmount and remove obsolete lease state."""

    mount = DirectNASMount.objects.select_for_update().filter(pk=mount_id).first()
    if mount is None or mount.cleanup_node_task_id != cleanup_task_id:
        return 0
    leases = DirectNASMountLease.objects.select_for_update().filter(mount_id=mount_id)
    has_active = leases.filter(status=DirectNASMountLease.Status.ACTIVE).exists()
    leases.exclude(status=DirectNASMountLease.Status.ACTIVE).delete()
    if not has_active:
        mount.delete()
        return 1
    mount.cleanup_node_task_id = None
    mount.cleanup_after = None
    mount.last_error = ""
    mount.save(
        update_fields=[
            "cleanup_node_task_id",
            "cleanup_after",
            "last_error",
            "updated_at",
        ]
    )
    return 1


@transaction.atomic
def _retry_failed_cleanup_task(
    *,
    mount_id: int,
    cleanup_task_id: UUID,
    error: str,
) -> int:
    """Return one failed unmount to bounded retry state."""

    mount = DirectNASMount.objects.select_for_update().filter(pk=mount_id).first()
    if mount is None or mount.cleanup_node_task_id != cleanup_task_id:
        return 0
    now = timezone.now()
    DirectNASMountLease.objects.filter(
        mount_id=mount_id,
        cleanup_node_task_id=cleanup_task_id,
        status=DirectNASMountLease.Status.CLEANUP_PENDING,
    ).update(
        status=DirectNASMountLease.Status.RELEASED,
        cleanup_node_task_id=None,
        last_error=error[:2000],
        updated_at=now,
    )
    has_active = DirectNASMountLease.objects.filter(
        mount_id=mount_id,
        status=DirectNASMountLease.Status.ACTIVE,
    ).exists()
    mount.cleanup_node_task_id = None
    mount.cleanup_after = (
        None
        if has_active
        else now + timedelta(seconds=conf.DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS)
    )
    mount.last_error = error[:2000]
    mount.save(
        update_fields=[
            "cleanup_node_task_id",
            "cleanup_after",
            "last_error",
            "updated_at",
        ]
    )
    return 1


def _dispatch_due_cleanups(*, limit: int) -> int:
    now = timezone.now()
    mount_ids = list(
        DirectNASMount.objects.filter(
            cleanup_node_task_id__isnull=True,
            cleanup_after__lte=now,
        )
        .order_by("cleanup_after", "id")
        .values_list("id", flat=True)[:limit]
    )
    dispatched = 0
    for mount_id in mount_ids:
        try:
            if _dispatch_group(mount_id=int(mount_id)):
                dispatched += 1
        except Exception as exc:
            logger.exception(
                "direct NAS mount cleanup dispatch failed mount_id=%s", mount_id
            )
            _defer_cleanup_dispatch(mount_id=int(mount_id), error=str(exc))
    return dispatched


def _retry_missing_cleanup_task(*, mount_id: int, cleanup_task_id: UUID) -> int:
    """Recover when a referenced NodeTask was removed with its Agent."""

    return _retry_failed_cleanup_task(
        mount_id=mount_id,
        cleanup_task_id=cleanup_task_id,
        error="NAS mount cleanup task record is unavailable.",
    )


@transaction.atomic
def _defer_cleanup_dispatch(*, mount_id: int, error: str) -> None:
    mount = DirectNASMount.objects.select_for_update().filter(pk=mount_id).first()
    if mount is None or mount.cleanup_node_task_id is not None:
        return
    has_active = DirectNASMountLease.objects.filter(
        mount=mount,
        status=DirectNASMountLease.Status.ACTIVE,
    ).exists()
    mount.cleanup_after = (
        None
        if has_active
        else timezone.now()
        + timedelta(seconds=conf.DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS)
    )
    mount.last_error = (error or "NAS mount cleanup dispatch failed.")[:2000]
    mount.save(update_fields=["cleanup_after", "last_error", "updated_at"])


@transaction.atomic
def _dispatch_group(*, mount_id: int) -> bool:
    mount = DirectNASMount.objects.select_for_update().filter(pk=mount_id).first()
    if mount is None or mount.cleanup_node_task_id is not None:
        return False
    if mount.cleanup_after is None or mount.cleanup_after > timezone.now():
        return False
    if DirectNASMountLease.objects.filter(
        mount=mount,
        status=DirectNASMountLease.Status.ACTIVE,
    ).exists():
        return False
    node = (
        Node.objects.filter(
            id=mount.reader_node_id,
            organization_id=mount.execution_organization_id,
            is_deleted=False,
        )
        .only("metadata")
        .first()
    )
    if node is None:
        _defer_locked_mount(mount=mount, error="Data Gateway Agent is unavailable.")
        return False
    if not node_supports_capability(node, NAS_MOUNT_LIFECYCLE_CAPABILITY):
        _defer_locked_mount(mount=mount, error=_UPGRADE_REQUIRED_ERROR)
        return False
    rows = list(
        DirectNASMountLease.objects.select_for_update().filter(
            mount=mount,
            status=DirectNASMountLease.Status.RELEASED,
        )
    )
    handle = run_agent_task_async(
        organization_id=mount.execution_organization_id,
        node_id=mount.reader_node_id,
        kind="nas.unmount",
        payload={"mount_point": mount.mount_point},
        correlation_type=_CLEANUP_CORRELATION,
        correlation_id=f"mount:{mount.id}",
        requesting_organization_id=mount.requesting_organization_id,
    )
    DirectNASMountLease.objects.filter(id__in=[row.id for row in rows]).update(
        status=DirectNASMountLease.Status.CLEANUP_PENDING,
        cleanup_node_task_id=handle.task.id,
        last_error="",
        updated_at=timezone.now(),
    )
    mount.cleanup_node_task_id = handle.task.id
    mount.cleanup_after = None
    mount.last_error = ""
    mount.save(
        update_fields=[
            "cleanup_node_task_id",
            "cleanup_after",
            "last_error",
            "updated_at",
        ]
    )
    return True


def _defer_locked_mount(*, mount: DirectNASMount, error: str) -> None:
    """Defer a locked aggregate without creating a NodeTask."""

    mount.cleanup_after = timezone.now() + timedelta(
        seconds=conf.DIRECT_NAS_MOUNT_CLEANUP_RETRY_SECONDS
    )
    mount.last_error = error[:2000]
    mount.save(update_fields=["cleanup_after", "last_error", "updated_at"])
