from django.db import transaction

from celery import shared_task

from apps.protection.services.directory_size_estimate import (
    refresh_backup_config_directory_estimates_by_id,
)
from apps.task.models import Task

_REQUEUE_COUNTDOWN_SECONDS = 5


def _freeze_task_directory_size(*, task_uuid: str, du_total: int) -> None:
    with transaction.atomic():
        task = Task.objects.select_for_update().filter(
            task_uuid=task_uuid,
            task_type=Task.Type.BACKUP,
        ).first()
        if task is None:
            return
        result_payload = dict(task.result_payload) if isinstance(task.result_payload, dict) else {}
        result_payload["du_total"] = max(0, int(du_total))
        result_payload["du_total_known"] = True
        request_payload = dict(task.request_payload) if isinstance(task.request_payload, dict) else {}
        request_payload["du_total"] = max(0, int(du_total))
        request_payload["du_total_known"] = True
        task.result_payload = result_payload
        task.request_payload = request_payload
        task.save(update_fields=["request_payload", "result_payload", "updated_at"])


@shared_task(
    name="apps.protection.tasks.directory_size_estimate.refresh_backup_config_directory_estimates",
    soft_time_limit=360,
    time_limit=420,
)
def refresh_backup_config_directory_estimates_task(
    *,
    config_id: int,
    attempt: int = 1,
    force_refresh: bool = False,
    task_uuid: str | None = None,
) -> dict:
    result = refresh_backup_config_directory_estimates_by_id(
        config_id=int(config_id),
        attempt=int(attempt or 1),
        force_refresh=bool(force_refresh),
    )
    if task_uuid and result.get("status") == "ok" and result.get("du_total_known"):
        _freeze_task_directory_size(
            task_uuid=str(task_uuid),
            du_total=int(result.get("du_total") or 0),
        )
    if result.get("should_requeue"):
        next_attempt = int(result.get("attempt") or 1) + 1
        refresh_backup_config_directory_estimates_task.apply_async(
            kwargs={
                "config_id": int(config_id),
                "attempt": next_attempt,
                "force_refresh": False,
                "task_uuid": task_uuid,
            },
            countdown=_REQUEUE_COUNTDOWN_SECONDS,
        )
    return result
