"""Task lifecycle alert evaluation."""

from __future__ import annotations

from django.db.models import Q

from apps.alert.constants import AlertStatus, AlertType
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.lifecycle import fire_alert, resolve_alert
from apps.task.models import Task
from common.resource_scope import ResourceScope


def _task_event_type(task: Task) -> str:
    if task.status == Task.Status.FAILED:
        return "task_failed"
    if task.status == Task.Status.TIMEOUT:
        return "task_timeout"
    return f"task_{task.status}"


def _task_policies_for(task: Task):
    """Org TASK policies for the task tenant, plus platform-owned TASK policies."""
    return AlertPolicy.objects.filter(type=AlertType.TASK).filter(
        Q(
            owner_scope=ResourceScope.ORGANIZATION,
            organization_id=task.organization_id,
        )
        | Q(owner_scope=ResourceScope.PLATFORM, organization__isnull=True)
    )


def handle_task_event(task: Task) -> None:
    event_type = _task_event_type(task)
    task_type = task.task_type
    operation = getattr(task, "repository_operation", None) if task_type == Task.Type.REPOSITORY_OPERATION else None
    operation_type = operation.operation_type if operation is not None else None
    policies = _task_policies_for(task).filter(enabled=True)
    for policy in policies:
        rule = policy.trigger_rule or {}
        rule_task_type = rule.get("task_type")
        rule_event = rule.get("event_type")
        rule_operation_type = rule.get("operation_type")
        if rule_task_type and rule_task_type != task_type:
            continue
        if rule_event and rule_event != event_type:
            continue
        if rule_operation_type and rule_operation_type != operation_type:
            continue
        fire_alert(
            policy,
            resource=task,
            title=f"Task alert: {event_type}",
            message=task.error_message or f"Task {task.task_uuid} status={task.status}",
            alert_key=f"{task_type}:{operation_type or '-'}:{event_type}",
            metadata={
                "event_type": event_type,
                "task_type": task_type,
                "task_id": task.id,
                "task_uuid": str(task.task_uuid),
                "task_status": task.status,
                "operation_type": operation_type,
                "error_message": task.error_message,
            },
        )


def recover_task_alerts(task: Task) -> None:
    if task.status != Task.Status.SUCCESS:
        return
    task_ids = [str(task.id), str(task.task_uuid)]
    policies = {policy.id: policy for policy in _task_policies_for(task)}
    qs = AlertRecord.objects.filter(
        type=AlertType.TASK,
        policy_id__in=policies,
        resource_id__in=task_ids,
        status__in=[
            AlertStatus.PENDING,
            AlertStatus.FIRING,
            AlertStatus.ACKNOWLEDGED,
        ],
    ).filter(
        Q(
            owner_scope=ResourceScope.ORGANIZATION,
            organization_id=task.organization_id,
        )
        | Q(owner_scope=ResourceScope.PLATFORM, organization__isnull=True)
    )
    for alert in qs:
        # Legacy rules keep their existing default, but explicit opt-out applies
        # to every automatic recovery path. Manual resolution is independent.
        policy = policies[alert.policy_id]
        if alert.resource_type != policy.resource_type:
            continue
        if (policy.recovery_rule or {}).get("enabled") is False:
            continue
        rule = policy.trigger_rule or {}
        if rule.get("task_type") and rule["task_type"] != task.task_type:
            continue
        if rule.get("operation_type"):
            operation = getattr(task, "repository_operation", None)
            if operation is None or rule["operation_type"] != operation.operation_type:
                continue
        metadata = alert.metadata or {}
        if metadata.get("task_uuid") and metadata["task_uuid"] != str(task.task_uuid):
            continue
        resolve_alert(alert)


def on_task_finished(task_uuid: str | None = None, task_id: int | None = None) -> None:
    queryset = Task.objects.all()
    if task_uuid:
        task = queryset.filter(task_uuid=task_uuid).first()
    elif task_id is not None:
        task = queryset.filter(id=task_id).first()
    else:
        task = None
    if task is None:
        return
    if task.status in (Task.Status.FAILED, Task.Status.TIMEOUT):
        handle_task_event(task)
    elif task.status == Task.Status.SUCCESS:
        recover_task_alerts(task)
