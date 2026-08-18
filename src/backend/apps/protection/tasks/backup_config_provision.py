from __future__ import annotations

from celery import shared_task

from apps.protection.services.backup_config_provision import (
    reconcile_backup_config_provision_tasks,
    run_backup_config_provision_task,
)
from common.observability.celery_context import celery_trace


@shared_task(
    name="apps.protection.tasks.backup_config_provision.execute_backup_config_provision_task",
)
def execute_backup_config_provision_task(*, task_id: int) -> dict[str, object]:
    with celery_trace(
        str(task_id),
        task_name="apps.protection.tasks.backup_config_provision.execute_backup_config_provision_task",
    ):
        return run_backup_config_provision_task(task_id=int(task_id))


@shared_task(
    name="apps.protection.tasks.backup_config_provision.reconcile_backup_config_provision_tasks",
)
def reconcile_backup_config_provision_tasks_task(
    *, limit: int = 100
) -> dict[str, int]:
    return reconcile_backup_config_provision_tasks(limit=int(limit))
