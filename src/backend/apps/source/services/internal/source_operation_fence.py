"""Database-backed exclusion between Source control and product operations."""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
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
    "active_source_control_task",
    "assert_source_product_operation_allowed",
    "lock_source_identity",
]
