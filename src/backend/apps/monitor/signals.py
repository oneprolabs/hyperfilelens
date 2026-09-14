"""Project stable task lifecycle signals into the operational event feed."""

from __future__ import annotations

from django.db import transaction
from django.dispatch import receiver

from apps.monitor.models import OperationalEvent
from apps.monitor.services.events import record_operational_event
from apps.task.models import Task
from apps.task.signals import task_failed, task_timed_out


def _record_task_terminal_event(*, task_uuid: str, severity: str) -> None:
    task = Task.objects.filter(task_uuid=task_uuid).first()
    if task is None or task.organization_id is None:
        return
    status_label = task.get_status_display().lower()
    type_label = task.get_task_type_display()

    def _record_event() -> None:
        record_operational_event(
            organization_id=task.organization_id,
            event_type=f"task.{task.status}",
            category=OperationalEvent.Category.PROTECTION,
            severity=severity,
            title=f"{type_label} {status_label}: {task.display_name}",
            details=task.error_message or "",
            occurred_at=task.finished_at,
            resource_type="task",
            resource_id=str(task.task_uuid),
            resource_name=task.display_name,
            source="task",
            target_path=f"/ops/tasks?taskUuid={task.task_uuid}",
            correlation_id=str(task.task_uuid),
            dedup_key=f"task:{task.task_uuid}:{task.status}",
            metadata={"task_type": task.task_type, "status": task.status},
        )

    transaction.on_commit(_record_event, robust=True)


@receiver(task_failed)
def record_failed_task_event(sender, task_uuid: str, **kwargs) -> None:
    _record_task_terminal_event(
        task_uuid=task_uuid,
        severity=OperationalEvent.Severity.WARNING,
    )


@receiver(task_timed_out)
def record_timed_out_task_event(sender, task_uuid: str, **kwargs) -> None:
    _record_task_terminal_event(
        task_uuid=task_uuid,
        severity=OperationalEvent.Severity.CRITICAL,
    )
