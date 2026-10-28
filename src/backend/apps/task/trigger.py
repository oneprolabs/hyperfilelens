from __future__ import annotations

from django.core.exceptions import ObjectDoesNotExist

from apps.task.models import Task


def task_trigger_source(task: Task) -> str:
    """Resolve the original source, including legacy retry-only records."""
    current = task
    visited: set[int] = set()
    while current.trigger_type == Task.TriggerType.RETRY:
        if current.pk in visited:
            break
        visited.add(current.pk)
        parent = current.replaces_task if current.replaces_task_id else None
        if (
            parent is None
            or parent.organization_id != task.organization_id
            or parent.task_type != task.task_type
        ):
            break
        current = parent
    if current.trigger_type != Task.TriggerType.RETRY:
        return current.trigger_type

    # Old scheduled maintenance retries had no replacement link. Do not infer
    # relationships from timestamps or names: only recover their known source.
    if task.task_type == Task.Type.REPOSITORY_OPERATION:
        try:
            operation = current.repository_operation
        except ObjectDoesNotExist:
            operation = None
        if operation is not None and (
            operation.operation_type in {"maintenance.quick", "maintenance.full"}
            or operation.triggered_by_task_id
            or (
                operation.operation_type == "snapshot.reconcile"
                and operation.requested_by_id is None
            )
        ):
            return Task.TriggerType.SYSTEM
    return Task.TriggerType.MANUAL
