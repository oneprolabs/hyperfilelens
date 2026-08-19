from __future__ import annotations

from celery import shared_task

from common.observability.celery_context import logged_celery_task

from apps.task.models import Task
from apps.protection.services.snapshot_download import (
    cleanup_expired_snapshot_download_artifacts,
    run_snapshot_download_task,
)


@shared_task(
    name="apps.protection.tasks.snapshot_download.execute_snapshot_download_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
@logged_celery_task(
    name="apps.protection.tasks.snapshot_download.execute_snapshot_download_task",
    trace_keys=("task_id",),
)
def execute_snapshot_download_task(self, *, task_id: int) -> dict:
    task = Task.objects.get(id=int(task_id))
    return run_snapshot_download_task(task=task)


@shared_task(name="apps.protection.tasks.snapshot_download.cleanup_snapshot_download_artifacts")
@logged_celery_task(
    name="apps.protection.tasks.snapshot_download.cleanup_snapshot_download_artifacts",
    trace_keys=(),
)
def cleanup_snapshot_download_artifacts(*, limit: int = 1000) -> dict[str, int]:
    return {"cleaned": cleanup_expired_snapshot_download_artifacts(limit=limit)}
