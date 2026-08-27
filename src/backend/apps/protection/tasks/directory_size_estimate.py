from django.db import transaction

from celery import shared_task

from apps.node import conf as node_conf
from apps.protection.services.directory_size_estimate import (
    enqueue_backup_config_directory_estimates,
    mark_backup_config_pending_estimates_unavailable,
    reconcile_directory_size_estimate,
)
from apps.task.models import Task


def _freeze_task_directory_size(*, task_uuid: str, du_total: int) -> None:
    with transaction.atomic():
        task = (
            Task.objects.select_for_update()
            .filter(
                task_uuid=task_uuid,
                task_type=Task.Type.BACKUP,
                status__in=(
                    Task.Status.PENDING,
                    Task.Status.WAITING,
                    Task.Status.RUNNING,
                ),
            )
            .first()
        )
        if task is None:
            return
        result_payload = (
            dict(task.result_payload) if isinstance(task.result_payload, dict) else {}
        )
        if result_payload.get("du_total_known"):
            return
        result_payload["du_total"] = max(0, int(du_total))
        result_payload["du_total_known"] = True
        request_payload = (
            dict(task.request_payload) if isinstance(task.request_payload, dict) else {}
        )
        request_payload["du_total"] = max(0, int(du_total))
        request_payload["du_total_known"] = True
        task.result_payload = result_payload
        task.request_payload = request_payload
        task.save(update_fields=["request_payload", "result_payload", "updated_at"])


@shared_task(
    name="apps.protection.tasks.directory_size_estimate.refresh_backup_config_directory_estimates",
)
def refresh_backup_config_directory_estimates_task(
    *,
    config_id: int,
    attempt: int = 1,
    force_refresh: bool = False,
    task_uuid: str | None = None,
) -> dict:
    result = enqueue_backup_config_directory_estimates(
        config_id=int(config_id),
        force_refresh=bool(force_refresh),
        task_uuid=task_uuid,
    )
    normalized_attempt = max(1, int(attempt or 1))
    if (
        result.get("status") in {"resolve_failed", "dispatch_failed"}
        and normalized_attempt < node_conf.PATH_SIZE_MAX_RETRIES
    ):
        refresh_backup_config_directory_estimates_task.apply_async(
            kwargs={
                "config_id": int(config_id),
                "attempt": normalized_attempt + 1,
                "force_refresh": False,
                "task_uuid": task_uuid,
            },
            countdown=30,
        )
    elif result.get("status") in {"resolve_failed", "dispatch_failed"}:
        mark_backup_config_pending_estimates_unavailable(config_id=int(config_id))
    return result


@shared_task(
    name="apps.protection.tasks.directory_size_estimate.reconcile_directory_size_estimate",
)
def reconcile_directory_size_estimate_task(
    *,
    config_id: int,
    directory_id: int,
    node_task_id: str,
    correlation_id: str,
    task_uuid: str | None = None,
) -> dict:
    return reconcile_directory_size_estimate(
        config_id=int(config_id),
        directory_id=int(directory_id),
        node_task_id=str(node_task_id),
        correlation_id=correlation_id,
        task_uuid=task_uuid,
    )
