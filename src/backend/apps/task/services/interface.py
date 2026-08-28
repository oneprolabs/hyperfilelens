from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.task.constants import RESTORE_TASK_TYPES
from apps.task.models import Task, TaskDependency, TaskEvent, TaskResource, TaskStep
from apps.task.signals import (
    task_cancelled,
    task_failed,
    task_succeeded,
    task_timed_out,
    task_updated,
)


TERMINAL_STATUSES = {
    Task.Status.SUCCESS,
    Task.Status.FAILED,
    Task.Status.CANCELLED,
    Task.Status.TIMEOUT,
}
TASK_CREATION_TRIGGER_MAP = {
    Task.TriggerType.MANUAL: Task.TriggerType.MANUAL,
    Task.TriggerType.SYSTEM: Task.TriggerType.SYSTEM,
    Task.TriggerType.RETRY: Task.TriggerType.MANUAL,
    Task.TriggerType.API: Task.TriggerType.MANUAL,
    Task.TriggerType.HOOK: Task.TriggerType.MANUAL,
}

BACKUP_SOURCE_ONLY_TASK_TYPES = frozenset(
    {
        Task.Type.BACKUP,
        Task.Type.RESTORE,
        Task.Type.INSIGHT_WORKSPACE_RESTORE,
        Task.Type.SNAPSHOT_DOWNLOAD,
        Task.Type.SNAPSHOT_DELETE,
        Task.Type.BACKUP_CONFIG_RESET,
        Task.Type.SOURCE_UNREGISTER,
    }
)


def _progress(value: int | float | Decimal) -> Decimal:
    number = Decimal(str(value)).quantize(Decimal("0.01"))
    if number < 0 or number > 100:
        raise ValidationError("progress must be between 0 and 100")
    return number


def _default_steps(task_type: str) -> list[str]:
    if task_type == Task.Type.REPOSITORY_OPERATION:
        return [
            "prepare_repository_operation",
            "verify_repository_owner",
            "run_repository_operation",
            "refresh_repository_usage",
            "finalize_repository_operation",
        ]
    if task_type == Task.Type.BACKUP_CONFIG_PROVISION:
        return [
            "validate_repository_target",
            "initialize_repository",
            "activate_backup_config",
        ]
    if task_type in RESTORE_TASK_TYPES:
        return ["restore", "finalize"]
    return ["snapshot", "scan", "chunk", "upload", "finalize"]


def append_task_event(
    *,
    task: Task,
    level: str = TaskEvent.Level.INFO,
    message: str,
    metadata: dict[str, Any] | None = None,
    step: TaskStep | None = None,
) -> TaskEvent:
    next_seq = (
        TaskEvent.objects.filter(task=task).aggregate(max_seq=Max("seq"))["max_seq"]
        or 0
    ) + 1
    return TaskEvent.objects.create(
        task=task,
        step=step,
        seq=next_seq,
        level=level,
        message=message,
        metadata=metadata or None,
    )


def _task_step(task: Task, step_name: str | None) -> TaskStep | None:
    if not step_name:
        return None
    return (
        TaskStep.objects.filter(task=task, step_name=step_name)
        .order_by("step_index", "id")
        .first()
    )


def _first_task_step(task: Task) -> TaskStep | None:
    return task.steps.order_by("step_index", "id").first()


def _current_task_step(task: Task) -> TaskStep | None:
    return _task_step(task, task.current_step) or _first_task_step(task)


def append_task_step_event(
    *,
    task: Task,
    step_name: str | None,
    level: str = TaskEvent.Level.INFO,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> TaskEvent:
    step = _task_step(task, step_name)
    if step_name and step is None:
        raise ValidationError({"step_name": f"Task step '{step_name}' was not found."})
    return append_task_event(
        task=task,
        step=step,
        level=level,
        message=message,
        metadata=metadata,
    )


@transaction.atomic
def create_task(
    *,
    organization_id: int | None,
    task_type: str,
    display_name: str,
    trigger_type: str = Task.TriggerType.MANUAL,
    request_payload: dict[str, Any] | None = None,
    resources: list[dict[str, Any]] | None = None,
    steps: list[dict[str, Any] | str] | None = None,
    normalize_trigger_type: bool = True,
    replaces_task: Task | None = None,
    group_uuid: UUID | str | None = None,
    idempotency_key: str | None = None,
) -> Task:
    if task_type not in {value for value, _label in Task.Type.choices}:
        raise ValidationError({"task_type": "Unsupported task type."})
    if trigger_type not in {value for value, _label in Task.TriggerType.choices}:
        raise ValidationError({"trigger_type": "Unsupported trigger type."})
    if normalize_trigger_type:
        trigger_type = TASK_CREATION_TRIGGER_MAP.get(
            trigger_type, Task.TriggerType.MANUAL
        )
    clean_name = display_name.strip()
    if not clean_name:
        raise ValidationError({"display_name": "Display name is required."})
    if replaces_task is not None:
        if replaces_task.pk is None:
            raise ValidationError(
                {"replaces_task": "The interrupted task must be persisted."}
            )
        replaces_task = Task.objects.select_for_update().get(pk=replaces_task.pk)
        if replaces_task.organization_id != organization_id:
            raise ValidationError(
                {
                    "replaces_task": "Replacement tasks must belong to the same organization."
                }
            )
        if replaces_task.task_type != task_type:
            raise ValidationError(
                {"replaces_task": "Replacement tasks must use the same task type."}
            )
        if replaces_task.status != Task.Status.FAILED:
            raise ValidationError(
                {
                    "replaces_task": "The interrupted task must be failed before replacement."
                }
            )
        if Task.objects.filter(replaces_task_id=replaces_task.id).exists():
            raise ValidationError(
                {"replaces_task": "The interrupted task already has a replacement."}
            )

    resource_defs = resources or []
    primary_resources = [
        resource for resource in resource_defs if resource.get("is_primary")
    ]
    if len(primary_resources) > 1:
        raise ValidationError(
            {"resources": "A task can have at most one primary resource."}
        )
    if task_type in BACKUP_SOURCE_ONLY_TASK_TYPES:
        resource_defs = [
            resource
            for resource in resource_defs
            if str(resource.get("resource_type") or "")
            == TaskResource.Type.BACKUP_SOURCE
        ]

    step_defs = steps or _default_steps(task_type)
    first_step = (
        step_defs[0].get("step_name")
        if step_defs and isinstance(step_defs[0], dict)
        else step_defs[0]
        if step_defs
        else None
    )
    task = Task.objects.create(
        organization_id=organization_id,
        task_type=task_type,
        display_name=clean_name,
        trigger_type=trigger_type,
        request_payload=request_payload or None,
        current_step=str(first_step) if first_step else None,
        replaces_task=replaces_task,
        recovery_attempt=(
            int(replaces_task.recovery_attempt) + 1 if replaces_task else 0
        ),
        group_uuid=group_uuid,
        idempotency_key=(idempotency_key.strip() if idempotency_key else None),
    )

    for resource in resource_defs:
        TaskResource.objects.create(
            task=task,
            resource_type=str(resource["resource_type"]),
            resource_subtype=str(resource.get("resource_subtype") or ""),
            resource_id=int(resource["resource_id"]),
            is_primary=bool(resource.get("is_primary")),
        )

    for idx, step_def in enumerate(step_defs, start=1):
        if isinstance(step_def, str):
            name = step_def
            status = TaskStep.Status.PENDING
            progress = Decimal("0.00")
        else:
            name = str(step_def["step_name"])
            status = str(step_def.get("status") or TaskStep.Status.PENDING)
            progress = _progress(step_def.get("progress") or 0)
        TaskStep.objects.create(
            task=task,
            step_index=idx,
            step_name=name,
            status=status,
            progress=progress,
        )

    append_task_event(
        task=task,
        step=_task_step(task, str(first_step) if first_step else None),
        message="Task created",
        metadata={"task_type": task.task_type, "trigger_type": task.trigger_type},
    )
    task_updated.send(
        sender=Task,
        task_uuid=str(task.task_uuid),
        organization_id=task.organization_id,
        status=task.status,
        progress=float(task.progress),
    )
    return task


def create_restore_task(
    *,
    organization_id: int,
    restore_plan_id: int | None = None,
    snapshot_id: int | None = None,
    snapshot_ids: list[int] | None = None,
    selection: dict[str, Any] | None = None,
    mode: str = "original",
    target: dict[str, Any] | None = None,
    conflict: str = "overwrite",
    restore_acl: bool = True,
    source_type: str | None = None,
    source_ref_id: int | None = None,
) -> Task:
    snapshots = [sid for sid in (snapshot_ids or []) if sid]
    if snapshot_id and snapshot_id not in snapshots:
        snapshots.insert(0, snapshot_id)
    resources = []
    if source_type and source_ref_id:
        resources.append(
            {
                "resource_type": TaskResource.Type.BACKUP_SOURCE,
                "resource_subtype": source_type,
                "resource_id": source_ref_id,
                "is_primary": True,
            }
        )
    payload = {
        "restore_plan_id": restore_plan_id,
        "snapshot_id": snapshot_id,
        "snapshot_ids": snapshots,
        "snapshot_count": len(snapshots),
        "selection": selection or {},
        "mode": mode,
        "target": target or {},
        "conflict": conflict,
        "restore_acl": bool(restore_acl),
    }
    return create_task(
        organization_id=organization_id,
        task_type=Task.Type.RESTORE,
        display_name=f"Restore plan #{restore_plan_id}"
        if restore_plan_id
        else "Restore task",
        trigger_type=Task.TriggerType.MANUAL,
        request_payload=payload,
        resources=resources,
        steps=["restore", "finalize"],
    )


@transaction.atomic
def cancel_task(
    *, task_uuid: UUID | str, organization_id: int, reason: str = ""
) -> Task:
    task = (
        Task.objects.select_for_update()
        .filter(task_uuid=task_uuid, organization_id=organization_id)
        .first()
    )
    if task is None:
        raise Task.DoesNotExist
    if task.status in TERMINAL_STATUSES:
        raise ValidationError("Finished tasks cannot be cancelled.")
    if task.task_type in RESTORE_TASK_TYPES:
        raise ValidationError(
            "Restore tasks must be cancelled through the restore service."
        )
    if task.task_type == Task.Type.SOURCE_UNREGISTER:
        raise ValidationError(
            "Source deregistration tasks cannot be cancelled. Submit a new request "
            "if cleanup did not start."
        )
    if task.task_type == Task.Type.BACKUP_CONFIG_PROVISION:
        raise ValidationError(
            "Storage validation is controlled by the backup configuration workflow."
        )
    if task.task_type == Task.Type.NODE_LIFECYCLE:
        raise ValidationError(
            "Node lifecycle tasks are controlled by the authoritative Agent task."
        )

    return finalize_task_cancellation(task=task, reason=reason)


def finalize_task_cancellation(*, task: Task, reason: str = "") -> Task:
    """Finalize cancellation after the owning domain has locked its task."""

    task.status = Task.Status.CANCELLED
    task.error_code = "TASK_CANCELLED"
    task.error_message = reason.strip() or "Task cancelled by user"
    task.finished_at = timezone.now()
    task.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "finished_at",
            "updated_at",
        ]
    )
    TaskStep.objects.filter(
        task=task,
        status__in=[TaskStep.Status.PENDING, TaskStep.Status.RUNNING],
    ).update(status=TaskStep.Status.SKIPPED)
    TaskDependency.objects.filter(task=task, is_active=True).update(
        is_active=False,
        resolved_at=task.finished_at,
    )
    append_task_event(
        task=task,
        step=_current_task_step(task),
        level=TaskEvent.Level.WARN,
        message="Task cancelled",
        metadata={"reason": task.error_message},
    )
    task_cancelled.send(
        sender=Task,
        task_uuid=str(task.task_uuid),
        organization_id=task.organization_id,
        status=task.status,
    )
    task_updated.send(
        sender=Task,
        task_uuid=str(task.task_uuid),
        organization_id=task.organization_id,
        status=task.status,
        progress=float(task.progress),
    )
    return task


@transaction.atomic
def retry_task(
    *, task_uuid: UUID | str, organization_id: int, reason: str = ""
) -> Task:
    task = (
        Task.objects.select_for_update()
        .filter(task_uuid=task_uuid, organization_id=organization_id)
        .first()
    )
    if task is None:
        raise Task.DoesNotExist
    if task.task_type in RESTORE_TASK_TYPES:
        raise ValidationError(
            "Restore tasks must be restarted from their originating restore workflow."
        )
    if task.task_type == Task.Type.NODE_LIFECYCLE:
        raise ValidationError(
            "Node lifecycle tasks cannot be retried from Operations. Retry the node removal instead."
        )
    if task.status not in {
        Task.Status.FAILED,
        Task.Status.TIMEOUT,
        Task.Status.CANCELLED,
    }:
        raise ValidationError(
            "Only failed, timeout, or cancelled tasks can be retried."
        )

    first_step = task.steps.order_by("step_index", "id").first()
    task.status = Task.Status.PENDING
    task.progress = Decimal("0.00")
    task.current_step = first_step.step_name if first_step else None
    task.retry_count += 1
    task.result_payload = None
    task.error_code = None
    task.error_message = None
    task.started_at = None
    task.finished_at = None
    task.save(
        update_fields=[
            "status",
            "progress",
            "current_step",
            "retry_count",
            "result_payload",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "updated_at",
        ]
    )
    TaskStep.objects.filter(task=task).update(
        status=TaskStep.Status.PENDING,
        progress=Decimal("0.00"),
    )
    append_task_event(
        task=task,
        step=first_step,
        message="Task queued for retry",
        metadata={"reason": reason.strip(), "retry_count": task.retry_count},
    )
    task_updated.send(
        sender=Task,
        task_uuid=str(task.task_uuid),
        organization_id=task.organization_id,
        status=task.status,
        progress=float(task.progress),
    )
    return task


@transaction.atomic
def start_task(*, task_uuid: UUID | str, organization_id: int) -> Task:
    task = (
        Task.objects.select_for_update()
        .filter(task_uuid=task_uuid, organization_id=organization_id)
        .first()
    )
    if task is None:
        raise Task.DoesNotExist
    if task.status != Task.Status.PENDING:
        raise ValidationError("Only pending tasks can be started.")
    task.status = Task.Status.RUNNING
    task.started_at = timezone.now()
    task.save(update_fields=["status", "started_at", "updated_at"])
    append_task_event(task=task, step=_current_task_step(task), message="Task started")
    task_updated.send(
        sender=Task,
        task_uuid=str(task.task_uuid),
        organization_id=task.organization_id,
        status=task.status,
        progress=float(task.progress),
    )
    return task


@transaction.atomic
def resume_waiting_task(*, task_uuid: UUID | str, organization_id: int) -> Task:
    """Move a dependency-free waiting task into execution."""
    task = (
        Task.objects.select_for_update()
        .filter(task_uuid=task_uuid, organization_id=organization_id)
        .first()
    )
    if task is None:
        raise Task.DoesNotExist
    if task.status not in {Task.Status.WAITING, Task.Status.BLOCKED}:
        raise ValidationError("Only waiting or blocked tasks can be resumed.")
    if task.dependencies.filter(is_active=True).exists():
        raise ValidationError("Waiting task still has active dependencies.")
    task.status = Task.Status.RUNNING
    if task.started_at is None:
        task.started_at = timezone.now()
    task.error_code = None
    task.error_message = None
    task.save(
        update_fields=[
            "status",
            "started_at",
            "error_code",
            "error_message",
            "updated_at",
        ]
    )
    append_task_event(
        task=task,
        step=_current_task_step(task),
        message="Task dependencies resolved; execution started",
    )
    task_updated.send(
        sender=Task,
        task_uuid=str(task.task_uuid),
        organization_id=task.organization_id,
        status=task.status,
        progress=float(task.progress),
    )
    return task


@transaction.atomic
def complete_task(
    *,
    task_uuid: UUID | str,
    organization_id: int,
    status: str = Task.Status.SUCCESS,
    progress: int | float = 100,
    result_payload: dict[str, Any] | None = None,
    error_code: str = "",
    error_message: str = "",
    include_error_details_in_event: bool = True,
) -> Task:
    if status not in TERMINAL_STATUSES:
        raise ValidationError("complete_task requires a terminal status.")
    task = (
        Task.objects.select_for_update()
        .filter(task_uuid=task_uuid, organization_id=organization_id)
        .first()
    )
    if task is None:
        raise Task.DoesNotExist
    if task.status in TERMINAL_STATUSES:
        return task

    task.status = status
    task.progress = _progress(progress)
    task.result_payload = result_payload
    task.error_code = error_code or None
    task.error_message = error_message or None
    task.finished_at = timezone.now()
    if not task.started_at:
        task.started_at = task.finished_at
    task.save(
        update_fields=[
            "status",
            "progress",
            "result_payload",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "updated_at",
        ]
    )
    level = (
        TaskEvent.Level.INFO if status == Task.Status.SUCCESS else TaskEvent.Level.ERROR
    )
    append_task_event(
        task=task,
        step=_current_task_step(task),
        level=level,
        message=f"Task finished with status {status}",
        metadata=(
            {"error_code": error_code, "error_message": error_message}
            if include_error_details_in_event
            else None
        ),
    )
    task_updated.send(
        sender=Task,
        task_uuid=str(task.task_uuid),
        organization_id=task.organization_id,
        status=task.status,
        progress=float(task.progress),
    )
    emit_task_terminal_signal(task)
    return task


def emit_task_terminal_signal(task: Task) -> None:
    if task.status == Task.Status.SUCCESS:
        task_succeeded.send(
            sender=Task,
            task_uuid=str(task.task_uuid),
            organization_id=task.organization_id,
        )
    elif task.status == Task.Status.FAILED:
        task_failed.send(
            sender=Task,
            task_uuid=str(task.task_uuid),
            organization_id=task.organization_id,
        )
    elif task.status == Task.Status.TIMEOUT:
        task_timed_out.send(
            sender=Task,
            task_uuid=str(task.task_uuid),
            organization_id=task.organization_id,
        )
