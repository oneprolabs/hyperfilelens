"""Project authoritative node lifecycle work into the Operations task list."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.task.models import Task, TaskResource, TaskStep
from apps.task.signals import task_updated
from apps.task.services.interface import (
    append_task_step_event,
    complete_task,
    create_task,
    emit_task_terminal_signal,
    start_task,
)


_REMOVE_STEPS = (
    "prepare_node_remove",
    "dispatch_agent_uninstall",
    "cleanup_node_endpoint",
    "finalize_node_remove",
)
_UPGRADE_STEP_BY_PHASE = {
    "dispatching": "dispatch_agent_upgrade",
    "upgrading": "install_agent_upgrade",
    "restarting": "restart_agent",
    "verification_pending": "verify_agent_upgrade",
    "verifying": "verify_agent_upgrade",
    "success": "finalize_agent_upgrade",
    "failed": "finalize_agent_upgrade",
}
_UPGRADE_STEPS = tuple(dict.fromkeys(_UPGRADE_STEP_BY_PHASE.values()))
_UPGRADE_STEP_PROGRESS = {
    "dispatch_agent_upgrade": 10,
    "install_agent_upgrade": 35,
    "restart_agent": 60,
    "verify_agent_upgrade": 80,
    "finalize_agent_upgrade": 100,
}
_UPGRADE_STATUS_MAP = {
    NodeTask.Status.PENDING: Task.Status.PENDING,
    NodeTask.Status.RUNNING: Task.Status.RUNNING,
    NodeTask.Status.SUCCESS: Task.Status.SUCCESS,
    NodeTask.Status.FAILED: Task.Status.FAILED,
    NodeTask.Status.TIMEOUT: Task.Status.TIMEOUT,
    NodeTask.Status.CANCELED: Task.Status.CANCELLED,
}
_TERMINAL_TASK_STATUSES = {
    Task.Status.SUCCESS,
    Task.Status.FAILED,
    Task.Status.CANCELLED,
    Task.Status.TIMEOUT,
}
_TERMINAL_NODE_TASK_STATUSES = {
    NodeTask.Status.SUCCESS,
    NodeTask.Status.FAILED,
    NodeTask.Status.CANCELED,
    NodeTask.Status.TIMEOUT,
}


def _is_direct_console_remove(node_task: NodeTask) -> bool:
    payload = node_task.payload if isinstance(node_task.payload, dict) else {}
    return (
        node_task.correlation_type == node_conf.LIFECYCLE_CORRELATION_TYPE
        and node_task.kind == "agent.uninstall"
        and node_task.node.role in {NodeRole.PROXY, NodeRole.GATEWAY}
        and not payload.get("source_unregister_task_id")
    )


def _node_snapshot(node: Node) -> dict[str, Any]:
    return {
        "id": int(node.id),
        "name": str(node.name or node.id),
        "role": str(node.role or ""),
        "endpoint": str(node.ip_address or ""),
        "registered_at": node.created_at.isoformat() if node.created_at else None,
    }


def _display_name(node: Node) -> str:
    kind = "Data Gateway" if node.role == NodeRole.GATEWAY else "Proxy Host"
    return f'Delete {kind} "{node.name or node.id}"'


def _operation_task(*, node_task: NodeTask) -> Task:
    existing = Task.objects.select_for_update().filter(
        organization_id=node_task.organization_id,
        task_type=Task.Type.NODE_LIFECYCLE,
        request_payload__node_task_id=str(node_task.id),
    ).first()
    if existing is not None:
        return existing
    return create_task(
        organization_id=node_task.organization_id,
        task_type=Task.Type.NODE_LIFECYCLE,
        display_name=_display_name(node_task.node),
        trigger_type=Task.TriggerType.MANUAL,
        request_payload={
            "operation": "remove",
            "node_task_id": str(node_task.id),
            "force": bool((node_task.payload or {}).get("force_cleanup")),
            "node": _node_snapshot(node_task.node),
        },
        resources=[
            {
                "resource_type": TaskResource.Type.HOST,
                "resource_subtype": str(node_task.node.role or ""),
                "resource_id": int(node_task.node_id),
                "is_primary": True,
            }
        ],
        steps=list(_REMOVE_STEPS),
    )


def _set_step(task: Task, step_name: str, status: str, progress: int) -> None:
    TaskStep.objects.filter(task=task, step_name=step_name).update(
        status=status,
        progress=Decimal("100.00")
        if status in {TaskStep.Status.SUCCESS, TaskStep.Status.WARNING}
        else Decimal(str(progress)),
    )
    task.current_step = step_name
    task.progress = Decimal(str(progress))
    task.save(update_fields=["current_step", "progress", "updated_at"])


def _result_payload(node_task: NodeTask) -> dict[str, Any]:
    result = dict(node_task.result or {})
    cleanup_complete = bool(
        result.get("cleanup_complete", node_task.status == NodeTask.Status.SUCCESS)
    )
    partial = node_task.status == NodeTask.Status.SUCCESS and not cleanup_complete
    return {
        **result,
        "node_task_id": str(node_task.id),
        "node_id": int(node_task.node_id),
        "node": _node_snapshot(node_task.node),
        "force": bool((node_task.payload or {}).get("force_cleanup")),
        "cleanup_complete": cleanup_complete,
        "cleanup_failures": [
            dict(item)
            for item in result.get("cleanup_failures") or []
            if isinstance(item, dict)
        ],
        "retained_resources": list(
            dict.fromkeys(
                str(item)
                for item in result.get("retained_resources") or []
                if str(item).strip()
            )
        ),
        "result": "partial_success"
        if partial
        else "success"
        if node_task.status == NodeTask.Status.SUCCESS
        else "failed",
    }


def _notify_task_after_commit(
    task: Task,
    *,
    emit_update: bool = True,
    emit_terminal: bool = False,
) -> None:
    task_uuid = str(task.task_uuid)
    organization_id = task.organization_id
    status = task.status
    progress = float(task.progress)

    def notify() -> None:
        if emit_update:
            task_updated.send(
                sender=Task,
                task_uuid=task_uuid,
                organization_id=organization_id,
                status=status,
                progress=progress,
            )
        if emit_terminal:
            emit_task_terminal_signal(task)

    if emit_update or emit_terminal:
        transaction.on_commit(notify)


def _upgrade_timeline(node_task: NodeTask) -> list[dict[str, Any]]:
    from apps.node.services.internal.node_lifecycle import _build_upgrade_timeline

    return _build_upgrade_timeline(node=node_task.node, task=node_task)


def _upgrade_result_payload(
    *, node_task: NodeTask, timeline: list[dict[str, Any]]
) -> dict[str, Any]:
    from apps.node.services.internal.node_lifecycle import (
        _source_version_from_task,
        _target_commit_from_task,
        _target_version_from_task,
    )

    result = node_task.result if isinstance(node_task.result, dict) else {}
    payload = {
        "node_task_id": str(node_task.id),
        "node_id": int(node_task.node_id),
        "node": _node_snapshot(node_task.node),
        "timeline": timeline,
        "source_version": _source_version_from_task(node_task),
        "target_version": _target_version_from_task(node_task),
        "target_commit": _target_commit_from_task(node_task) or None,
        "failure_code": result.get("failure_code")
        or result.get("diagnostic_error_code"),
        "observed_agent_version": result.get("observed_agent_version"),
        "observed_agent_commit": result.get("observed_agent_commit"),
    }
    progress = result.get("last_progress")
    download = progress.get("download") if isinstance(progress, dict) else None
    if isinstance(download, dict):
        allowed = {
            "state",
            "downloaded_bytes",
            "total_bytes",
            "bytes_per_second",
            "elapsed_seconds",
            "idle_seconds",
            "attempt",
            "next_attempt",
            "max_attempts",
            "retry_after_seconds",
            "reason",
        }
        payload["download"] = {
            key: download[key] for key in allowed if key in download
        }
    return payload


def create_node_upgrade_operation_task(
    *,
    node_task: NodeTask,
    target_version: str,
    target_commit: str = "",
) -> Task:
    """Create and bind the display task inside the upgrade transaction."""
    node = node_task.node
    if node_task.parent_task_id:
        linked_task = Task.objects.filter(
            pk=node_task.parent_task_id,
            organization_id=node_task.organization_id,
            task_type=Task.Type.NODE_LIFECYCLE,
        ).first()
        if linked_task is not None:
            return linked_task
    existing_task = (
        Task.objects.select_for_update()
        .filter(
            organization_id=node_task.organization_id,
            task_type=Task.Type.NODE_LIFECYCLE,
            request_payload__node_task_id=str(node_task.id),
        )
        .first()
    )
    if existing_task is not None:
        node_task.parent_task = existing_task
        node_task.save(update_fields=["parent_task", "updated_at"])
        return existing_task
    task = create_task(
        organization_id=node_task.organization_id,
        task_type=Task.Type.NODE_LIFECYCLE,
        display_name=f'Upgrade {node.get_role_display()} "{node.name or node.id}"',
        trigger_type=Task.TriggerType.MANUAL,
        request_payload={
            "operation": "upgrade",
            "node_task_id": str(node_task.id),
            "target_version": target_version,
            "target_commit": target_commit or None,
            "node": _node_snapshot(node),
        },
        resources=[
            {
                "resource_type": TaskResource.Type.HOST,
                "resource_subtype": str(node.role or ""),
                "resource_id": int(node.id),
                "is_primary": True,
            }
        ],
        steps=list(_UPGRADE_STEPS),
        notify_on_commit=True,
    )
    node_task.parent_task = task
    node_task.save(update_fields=["parent_task", "updated_at"])
    return task


def _upgrade_step_state(
    *, node_task: NodeTask
) -> tuple[str, int, dict[str, str], list[dict[str, Any]]]:
    timeline = _upgrade_timeline(node_task)
    step_statuses: dict[str, str] = {}
    current_step = "dispatch_agent_upgrade"
    progress = 0
    for phase in timeline:
        step_name = _UPGRADE_STEP_BY_PHASE.get(str(phase.get("phase") or ""))
        if not step_name:
            continue
        phase_status = str(phase.get("status") or "pending")
        if phase_status == "completed":
            step_statuses[step_name] = TaskStep.Status.SUCCESS
            progress = max(progress, _UPGRADE_STEP_PROGRESS[step_name])
        elif phase_status == "active":
            step_statuses[step_name] = TaskStep.Status.RUNNING
            current_step = step_name
            progress = max(progress, _UPGRADE_STEP_PROGRESS[step_name] - 5)
        elif phase_status == "failed":
            step_statuses[step_name] = TaskStep.Status.FAILED
            current_step = step_name
        elif step_name not in step_statuses:
            step_statuses[step_name] = TaskStep.Status.PENDING

    if node_task.status in _TERMINAL_NODE_TASK_STATUSES:
        current_step = "finalize_agent_upgrade"
        if node_task.status == NodeTask.Status.SUCCESS:
            step_statuses.update(
                {step_name: TaskStep.Status.SUCCESS for step_name in _UPGRADE_STEPS}
            )
        step_statuses[current_step] = (
            TaskStep.Status.SUCCESS
            if node_task.status == NodeTask.Status.SUCCESS
            else TaskStep.Status.SKIPPED
            if node_task.status == NodeTask.Status.CANCELED
            else TaskStep.Status.FAILED
        )
        if node_task.status == NodeTask.Status.SUCCESS:
            progress = 100
    return current_step, progress, step_statuses, timeline


@transaction.atomic
def sync_node_upgrade_operation_task(*, node_task: NodeTask) -> Task | None:
    """Project one formal Agent upgrade without changing its authority."""
    node_task = (
        NodeTask.objects.select_for_update()
        .select_related("node")
        .filter(
            pk=node_task.pk,
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            kind="agent.upgrade",
        )
        .first()
    )
    if node_task is None:
        return None
    projection_created = False
    if node_task.parent_task_id is None:
        from apps.node.services.internal.node_lifecycle import (
            _target_commit_from_task,
            _target_version_from_task,
        )

        create_node_upgrade_operation_task(
            node_task=node_task,
            target_version=_target_version_from_task(node_task),
            target_commit=_target_commit_from_task(node_task),
        )
        projection_created = True
    task = (
        Task.objects.select_for_update()
        .filter(
            pk=node_task.parent_task_id,
            organization_id=node_task.organization_id,
            task_type=Task.Type.NODE_LIFECYCLE,
        )
        .first()
    )
    if task is None or task.status in _TERMINAL_TASK_STATUSES:
        return task

    desired_status = _UPGRADE_STATUS_MAP[node_task.status]
    current_step, progress, step_statuses, timeline = _upgrade_step_state(
        node_task=node_task
    )
    now = timezone.now()
    result = dict(node_task.result or {})
    result_payload = _upgrade_result_payload(
        node_task=node_task,
        timeline=timeline,
    )
    error_code = None
    error_message = None
    if desired_status in {
        Task.Status.FAILED,
        Task.Status.TIMEOUT,
        Task.Status.CANCELLED,
    }:
        error_code = str(
            result.get("failure_code")
            or result.get("diagnostic_error_code")
            or (
                "NODE_UPGRADE_TIMEOUT"
                if desired_status == Task.Status.TIMEOUT
                else "NODE_UPGRADE_CANCELLED"
                if desired_status == Task.Status.CANCELLED
                else "NODE_UPGRADE_FAILED"
            )
        )
        error_message = str(node_task.last_error or "Agent upgrade did not complete.")

    changed = any(
        (
            task.status != desired_status,
            task.current_step != current_step,
            task.progress != Decimal(str(progress)),
            task.result_payload != result_payload,
            task.error_code != error_code,
            task.error_message != error_message,
        )
    )
    step_changed = False
    for step in task.steps.all():
        desired_step_status = step_statuses.get(step.step_name, TaskStep.Status.PENDING)
        desired_step_progress = Decimal(
            "100.00" if desired_step_status == TaskStep.Status.SUCCESS else "0.00"
        )
        if (
            step.status != desired_step_status
            or step.progress != desired_step_progress
        ):
            step.status = desired_step_status
            step.progress = desired_step_progress
            step.save(update_fields=["status", "progress"])
            step_changed = True
    if not changed and not step_changed:
        return task

    previous_status = task.status
    task.status = desired_status
    task.current_step = current_step
    task.progress = Decimal(str(progress))
    task.result_payload = result_payload
    task.error_code = error_code
    task.error_message = error_message
    if desired_status != Task.Status.PENDING:
        task.started_at = (
            task.started_at
            or node_task.accepted_at
            or node_task.dispatched_at
            or now
        )
    if desired_status in _TERMINAL_TASK_STATUSES:
        task.finished_at = node_task.updated_at or now
    task.save(
        update_fields=[
            "status",
            "current_step",
            "progress",
            "result_payload",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "updated_at",
        ]
    )
    if previous_status != desired_status:
        append_task_step_event(
            task=task,
            step_name=current_step,
            level=(
                "ERROR"
                if desired_status in {Task.Status.FAILED, Task.Status.TIMEOUT}
                else "WARN"
                if desired_status == Task.Status.CANCELLED
                else "INFO"
            ),
            message=f"Agent upgrade is {desired_status}",
            metadata={"node_task_id": str(node_task.id)},
        )
    _notify_task_after_commit(
        task,
        emit_update=not projection_created,
        emit_terminal=(
            previous_status not in _TERMINAL_TASK_STATUSES
            and desired_status in _TERMINAL_TASK_STATUSES
        ),
    )
    return task


def _reconcile_active_task(*, task: Task, status: str) -> None:
    """Repair a projection changed independently of its authoritative NodeTask."""
    if task.status == status:
        return
    now = timezone.now()
    task.status = status
    task.result_payload = None
    task.error_code = None
    task.error_message = None
    task.finished_at = None
    if status == Task.Status.PENDING:
        task.started_at = None
        task.progress = Decimal("0.00")
        first_step = task.steps.order_by("step_index", "id").first()
        task.current_step = first_step.step_name if first_step else None
        task.steps.update(status=TaskStep.Status.PENDING, progress=Decimal("0.00"))
    else:
        task.started_at = task.started_at or now
    task.save(
        update_fields=[
            "status",
            "progress",
            "current_step",
            "result_payload",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "updated_at",
        ]
    )
    append_task_step_event(
        task=task,
        step_name=task.current_step,
        level="WARN",
        message=f"Node removal projection reconciled to {status}",
        metadata={"authoritative_status": status},
    )
    task_updated.send(
        sender=Task,
        task_uuid=str(task.task_uuid),
        organization_id=task.organization_id,
        status=task.status,
        progress=float(task.progress),
    )


def _reconcile_terminal_task(
    *,
    task: Task,
    status: str,
    progress: int,
    result_payload: dict[str, Any],
    error_code: str,
    error_message: str,
) -> Task:
    """Refresh a terminal projection without replaying terminal side effects."""
    desired_progress = Decimal(str(progress))
    desired_error_code = error_code or None
    desired_error_message = error_message or None
    changed = any(
        (
            task.status != status,
            task.progress != desired_progress,
            task.result_payload != result_payload,
            task.error_code != desired_error_code,
            task.error_message != desired_error_message,
        )
    )
    if not changed:
        return task
    task.status = status
    task.progress = desired_progress
    task.result_payload = result_payload
    task.error_code = desired_error_code
    task.error_message = desired_error_message
    task.finished_at = timezone.now()
    task.started_at = task.started_at or task.finished_at
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
    append_task_step_event(
        task=task,
        step_name=task.current_step,
        level="INFO" if status == Task.Status.SUCCESS else "WARN",
        message="Node removal result reconciled from the authoritative Agent task",
        metadata={"authoritative_status": status},
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
def sync_node_remove_operation_task(*, node_task: NodeTask) -> Task | None:
    node_task = (
        NodeTask.objects.select_for_update()
        .select_related("node")
        .filter(pk=node_task.pk)
        .first()
    )
    if node_task is None or not _is_direct_console_remove(node_task):
        return None
    task = _operation_task(node_task=node_task)
    if node_task.status == NodeTask.Status.PENDING:
        if task.status in _TERMINAL_TASK_STATUSES:
            _reconcile_active_task(task=task, status=Task.Status.PENDING)
        return task
    task_was_terminal = task.status in _TERMINAL_TASK_STATUSES
    if task.status == Task.Status.PENDING:
        start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)
        task.refresh_from_db()
    elif task_was_terminal and node_task.status == NodeTask.Status.RUNNING:
        _reconcile_active_task(task=task, status=Task.Status.RUNNING)
    _set_step(task, "prepare_node_remove", TaskStep.Status.SUCCESS, 15)
    dispatched = bool(node_task.dispatched_at or node_task.accepted_at)
    if not dispatched and node_task.status in _TERMINAL_NODE_TASK_STATUSES:
        dispatch_status = (
            TaskStep.Status.SKIPPED
            if node_task.status == NodeTask.Status.CANCELED
            else TaskStep.Status.FAILED
        )
        _set_step(task, "dispatch_agent_uninstall", dispatch_status, 35)
    else:
        _set_step(task, "dispatch_agent_uninstall", TaskStep.Status.SUCCESS, 35)
    if node_task.status == NodeTask.Status.RUNNING:
        _set_step(task, "cleanup_node_endpoint", TaskStep.Status.RUNNING, 65)
        return task

    result_payload = _result_payload(node_task)
    cleanup_complete = bool(result_payload["cleanup_complete"])
    node_succeeded = node_task.status == NodeTask.Status.SUCCESS
    if not dispatched or node_task.status == NodeTask.Status.CANCELED:
        _set_step(task, "cleanup_node_endpoint", TaskStep.Status.SKIPPED, 35)
    else:
        _set_step(
            task,
            "cleanup_node_endpoint",
            (
                TaskStep.Status.SUCCESS
                if cleanup_complete
                else TaskStep.Status.WARNING
                if node_succeeded
                else TaskStep.Status.FAILED
            ),
            85,
        )
    terminal_progress = 100 if node_succeeded else 85 if dispatched else 35
    _set_step(
        task,
        "finalize_node_remove",
        TaskStep.Status.SUCCESS if node_succeeded else TaskStep.Status.SKIPPED,
        terminal_progress,
    )
    terminal_status = (
        Task.Status.SUCCESS
        if node_succeeded
        else Task.Status.CANCELLED
        if node_task.status == NodeTask.Status.CANCELED
        else Task.Status.TIMEOUT
        if node_task.status == NodeTask.Status.TIMEOUT
        else Task.Status.FAILED
    )
    error_code = "" if node_succeeded else "NODE_REMOVE_FAILED"
    error_message = (
        "" if node_succeeded else str(node_task.last_error or "Node removal failed.")
    )
    if task_was_terminal:
        return _reconcile_terminal_task(
            task=task,
            status=terminal_status,
            progress=terminal_progress,
            result_payload=result_payload,
            error_code=error_code,
            error_message=error_message,
        )
    event_step = "cleanup_node_endpoint" if dispatched else "dispatch_agent_uninstall"
    append_task_step_event(
        task=task,
        step_name=event_step,
        level="WARN"
        if node_succeeded and not cleanup_complete
        else "INFO"
        if node_succeeded
        else "ERROR",
        message=(
            "Node removal completed with retained physical resources"
            if node_succeeded and not cleanup_complete
            else "Node removal completed"
            if node_succeeded
            else "Agent uninstall dispatch failed"
            if not dispatched
            else "Node removal failed"
        ),
        metadata={
            "dispatched": dispatched,
            "cleanup_complete": cleanup_complete,
            "cleanup_failures": result_payload["cleanup_failures"],
            "retained_resources": result_payload["retained_resources"],
        },
    )
    return complete_task(
        task_uuid=task.task_uuid,
        organization_id=task.organization_id,
        status=terminal_status,
        progress=terminal_progress,
        result_payload=result_payload,
        error_code=error_code,
        error_message=error_message,
    )


@transaction.atomic
def record_immediate_node_remove_task(
    *,
    node: Node,
    force: bool,
    result: dict[str, Any],
) -> Task | None:
    if node.role not in {NodeRole.PROXY, NodeRole.GATEWAY}:
        return None
    operation_id = str(result.get("operation_id") or f"force-remove:{node.id}")
    existing = Task.objects.filter(
        organization_id=node.organization_id,
        task_type=Task.Type.NODE_LIFECYCLE,
        request_payload__operation_id=operation_id,
    ).first()
    if existing is not None:
        return existing
    task = create_task(
        organization_id=node.organization_id,
        task_type=Task.Type.NODE_LIFECYCLE,
        display_name=_display_name(node),
        trigger_type=Task.TriggerType.MANUAL,
        request_payload={
            "operation": "remove",
            "operation_id": operation_id,
            "force": bool(force),
            "node": _node_snapshot(node),
        },
        resources=[
            {
                "resource_type": TaskResource.Type.HOST,
                "resource_subtype": str(node.role or ""),
                "resource_id": int(node.id),
                "is_primary": True,
            }
        ],
        steps=list(_REMOVE_STEPS),
    )
    start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)
    task.refresh_from_db()
    _set_step(task, "prepare_node_remove", TaskStep.Status.SUCCESS, 20)
    _set_step(task, "dispatch_agent_uninstall", TaskStep.Status.SKIPPED, 40)
    _set_step(task, "cleanup_node_endpoint", TaskStep.Status.WARNING, 85)
    _set_step(task, "finalize_node_remove", TaskStep.Status.SUCCESS, 100)
    payload = {
        **result,
        "node": _node_snapshot(node),
        "result": "partial_success",
        "cleanup_complete": False,
    }
    return complete_task(
        task_uuid=task.task_uuid,
        organization_id=task.organization_id,
        status=Task.Status.SUCCESS,
        progress=100,
        result_payload=payload,
    )


__all__ = [
    "create_node_upgrade_operation_task",
    "record_immediate_node_remove_task",
    "sync_node_remove_operation_task",
    "sync_node_upgrade_operation_task",
]
