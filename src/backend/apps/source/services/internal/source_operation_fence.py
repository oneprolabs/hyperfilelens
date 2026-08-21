"""Database-backed exclusion between Source control and product operations."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone

from common.errors import AppError
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.protection import conf as protection_conf
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.task.constants import RESTORE_TASK_TYPES
from apps.task.models import Task, TaskResource


_ACTIVE_STATUSES = {
    Task.Status.PENDING,
    Task.Status.WAITING,
    Task.Status.BLOCKED,
    Task.Status.RUNNING,
}
_SOURCE_CONTROL_TASK_TYPES = {
    Task.Type.BACKUP_CONFIG_RESET,
    Task.Type.SOURCE_UNREGISTER,
}
_BACKUP_NODE_TASK_CORRELATION_TYPES = {
    protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
    protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE,
}


def _stopping_backup_task_uuids(*, organization_id: int) -> list[UUID]:
    cutoff = timezone.now() - timedelta(
        seconds=max(1, int(node_conf.TASK_CANCEL_GRACE_SECONDS))
    )
    values = NodeTask.objects.filter(
        organization_id=organization_id,
        correlation_type__in=_BACKUP_NODE_TASK_CORRELATION_TYPES,
        status__in=(NodeTask.Status.PENDING, NodeTask.Status.RUNNING),
        cancel_requested_at__gt=cutoff,
    ).values_list("correlation_id", flat=True)
    task_uuids: list[UUID] = []
    for value in values:
        try:
            task_uuids.append(UUID(str(value)))
        except (TypeError, ValueError, AttributeError):
            continue
    return task_uuids


def lock_source_identity(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> None:
    """Lock one Source identity using the ordering shared by unregister."""
    if source_type == "agent":
        exists = Node.objects.select_for_update().filter(
            organization_id=organization_id,
            role=NodeRole.AGENT,
            id=source_ref_id,
            is_deleted=False,
        ).exists()
    elif source_type == "nas":
        exists = SourceResource.all_objects.select_for_update().filter(
            organization_id=organization_id,
            resource_type=ResourceType.NAS,
            id=source_ref_id,
            is_deleted=False,
        ).exists()
    else:
        raise ValidationError({"source_type": "Unsupported backup source type."})
    if not exists:
        raise ValidationError({"source_ref_id": "Backup source not found."})


def active_source_control_task(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    allowed_task_id: int | None = None,
) -> Task | None:
    """Return an active Reset/Unregister task owning one Source."""
    tasks = Task.objects.filter(
        organization_id=organization_id,
        task_type__in=_SOURCE_CONTROL_TASK_TYPES,
        status__in=_ACTIVE_STATUSES,
        resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
        resources__resource_subtype=source_type,
        resources__resource_id=source_ref_id,
    )
    if allowed_task_id is not None:
        tasks = tasks.exclude(id=int(allowed_task_id))
    return tasks.order_by("created_at", "id").distinct().first()


def active_source_backup_task(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> Task | None:
    """Return an active or cancel-grace Backup task owning one Source.

    The request-payload fallback preserves compatibility with historical tasks
    created before backup-source TaskResource rows were consistently attached.
    """
    stopping_task_uuids = _stopping_backup_task_uuids(
        organization_id=organization_id
    )
    tasks = (
        Task.objects.filter(
            Q(status__in=_ACTIVE_STATUSES)
            | Q(status=Task.Status.CANCELLED, task_uuid__in=stopping_task_uuids),
            organization_id=organization_id,
            task_type=Task.Type.BACKUP,
        )
        .prefetch_related("resources")
        .order_by("created_at", "id")
    )
    for task in tasks:
        resources = list(task.resources.all())
        if any(
            resource.resource_type == TaskResource.Type.BACKUP_SOURCE
            and int(resource.resource_id) == int(source_ref_id)
            and (
                resource.resource_subtype == source_type
                or (source_type == "agent" and not resource.resource_subtype)
            )
            for resource in resources
        ):
            return task
        payload = task.request_payload if isinstance(task.request_payload, dict) else {}
        if (
            str(payload.get("source_type") or "") == source_type
            and int(payload.get("source_ref_id") or 0) == int(source_ref_id)
        ):
            return task
    return None


def product_task_is_stopping(*, organization_id: int, task: Task | None) -> bool:
    """Return whether a cancelled product task is still within cancel grace."""
    if task is None or task.status != Task.Status.CANCELLED:
        return False
    cutoff = timezone.now() - timedelta(
        seconds=max(1, int(node_conf.TASK_CANCEL_GRACE_SECONDS))
    )
    return NodeTask.objects.filter(
        organization_id=organization_id,
        correlation_type__in=_BACKUP_NODE_TASK_CORRELATION_TYPES,
        correlation_id=str(task.task_uuid),
        status__in=(NodeTask.Status.PENDING, NodeTask.Status.RUNNING),
        cancel_requested_at__gt=cutoff,
    ).exists()


def active_source_restore_task(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> Task | None:
    """Return an active Restore task owning one Source."""
    tasks = (
        Task.objects.filter(
            organization_id=organization_id,
            task_type__in=RESTORE_TASK_TYPES,
            status__in=_ACTIVE_STATUSES,
        )
        .prefetch_related("resources")
        .order_by("created_at", "id")
    )
    for task in tasks:
        if any(
            resource.resource_type == TaskResource.Type.BACKUP_SOURCE
            and int(resource.resource_id) == int(source_ref_id)
            and (
                resource.resource_subtype == source_type
                or (source_type == "agent" and not resource.resource_subtype)
            )
            for resource in task.resources.all()
        ):
            return task
    return None


def assert_no_active_backup_for_sources(
    *,
    organization_id: int,
    sources: list[tuple[str, int]],
) -> None:
    """Fence conflicting source mutations against active or stopping backups."""
    identities = sorted(
        {
            (str(source_type), int(source_ref_id))
            for source_type, source_ref_id in sources
        }
    )
    for source_type, source_ref_id in identities:
        lock_source_identity(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
        )
    for source_type, source_ref_id in identities:
        blocker = active_source_backup_task(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
        )
        if blocker is None:
            continue
        blocker_status = (
            "stopping"
            if product_task_is_stopping(
                organization_id=organization_id,
                task=blocker,
            )
            else blocker.status
        )
        raise AppError(
            code="BACKUP.ALREADY_RUNNING",
            status=409,
            title="Backup already running",
            diagnostic=(
                "A backup task is active for this source. Stop it or wait for "
                "it to finish before continuing."
            ),
            retryable=False,
            meta={
                "task_uuid": str(blocker.task_uuid),
                "task_id": blocker.id,
                "task_type": blocker.task_type,
                "display_name": blocker.display_name,
                "status": blocker_status,
                "source_type": source_type,
                "source_ref_id": source_ref_id,
                "created_at": blocker.created_at.isoformat()
                if blocker.created_at
                else "",
            },
        )


def assert_no_active_backup_for_source(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> None:
    assert_no_active_backup_for_sources(
        organization_id=organization_id,
        sources=[(source_type, source_ref_id)],
    )


def assert_no_active_restore_for_source(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> None:
    """Fence backup creation against an active Restore task."""
    lock_source_identity(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    blocker = active_source_restore_task(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if blocker is None:
        return
    raise AppError(
        code="RESTORE.ALREADY_RUNNING",
        status=409,
        title="Restore already running",
        diagnostic=(
            "A restore task is active for this source. Stop it or wait for it "
            "to finish before starting a backup."
        ),
        retryable=False,
        meta={
            "task_uuid": str(blocker.task_uuid),
            "task_id": blocker.id,
            "task_type": blocker.task_type,
            "display_name": blocker.display_name,
            "status": blocker.status,
            "source_type": source_type,
            "source_ref_id": source_ref_id,
            "created_at": blocker.created_at.isoformat()
            if blocker.created_at
            else "",
        },
    )


def assert_source_product_operation_allowed(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    allowed_task_id: int | None = None,
) -> None:
    """Fence product/lifecycle work against active Source control tasks."""
    lock_source_identity(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    blocker = active_source_control_task(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        allowed_task_id=allowed_task_id,
    )
    if blocker is None:
        return
    raise ValidationError(
        {
            "source_ref_id": (
                "Backup source has an active Reset or Deregistration operation. "
                "Wait for it to finish before starting new work."
            ),
            "task_uuid": str(blocker.task_uuid),
        }
    )


__all__ = [
    "active_source_backup_task",
    "active_source_control_task",
    "active_source_restore_task",
    "assert_no_active_backup_for_source",
    "assert_no_active_backup_for_sources",
    "assert_no_active_restore_for_source",
    "assert_source_product_operation_allowed",
    "lock_source_identity",
    "product_task_is_stopping",
]
