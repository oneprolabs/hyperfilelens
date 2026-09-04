from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.node.models import NodeTask
from apps.node.services.internal.repository_server import (
    repository_server_diagnostic_code,
    repository_server_public_error_message,
)
from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.restore.services.task_events import append_restore_item_terminal_event
from apps.restore.services.task_classification import normalize_restore_task_type
from apps.protection.services.progress.orchestrated_progress import (
    RESTORE_FINALIZE_START,
)
from apps.protection.services.snapshot_usage import release_restore_usage
from apps.storage.repositories.models import Repository
from apps.task.models import Task, TaskEvent, TaskStep
from apps.task.services.interface import (
    TERMINAL_STATUSES,
    append_task_step_event,
    complete_task,
    start_task,
)

_TERMINAL_NODE_STATUSES = {
    NodeTask.Status.SUCCESS,
    NodeTask.Status.FAILED,
    NodeTask.Status.TIMEOUT,
    NodeTask.Status.CANCELED,
}
_TERMINAL_RESTORE_ITEM_STATUSES = {
    RestoreRecordItem.Status.SUCCESS,
    RestoreRecordItem.Status.FAILED,
    RestoreRecordItem.Status.SKIPPED,
    RestoreRecordItem.Status.CANCELLED,
}
logger = logging.getLogger(__name__)


def _repository_server_failure_message(node_task: NodeTask) -> str:
    result = node_task.result if isinstance(node_task.result, dict) else {}
    message = str(node_task.last_error or "").strip()
    code = repository_server_diagnostic_code(result, message)
    if code:
        return repository_server_public_error_message(code)
    if not message:
        message = str(result.get("error") or "").strip()
    return message or "Restore repository server failed."


def _continue_lens_workspace_restore(record_id: int) -> None:
    """Best-effort fast path; periodic reconciliation is the durable fallback."""

    from apps.restore.services.interface import _dispatch_restore_items

    try:
        with transaction.atomic():
            record = RestoreRecord.objects.filter(pk=record_id).first()
            if record is None:
                return
            task = Task.objects.select_for_update().filter(
                pk=record.task_id,
                status__in=(Task.Status.PENDING, Task.Status.RUNNING),
            ).first()
            if task is None:
                return
            _dispatch_restore_items(
                organization_id=record.organization_id,
                record=record,
                task=task,
            )
    except Exception:
        logger.exception(
            "insight restore continuation dispatch failed record_id=%s",
            record_id,
        )


@receiver(post_save, sender=NodeTask)
@transaction.atomic
def sync_restore_record_from_node_task(
    sender: type[NodeTask], instance: NodeTask, **kwargs: Any
) -> None:
    if (
        instance.correlation_type == "restore.repository_server"
        and instance.correlation_id
    ):
        _handle_restore_repository_server_task(node_task=instance)
        return
    if instance.correlation_type != "restore.record" or not instance.correlation_id:
        return
    item_id = _payload_int(instance.payload, "restore_record_item_id")
    item = (
        RestoreRecordItem.objects.select_related("restore_record")
        .filter(id=item_id)
        .first()
        if item_id
        else None
    )
    record = item.restore_record if item is not None else None
    if record is None:
        return
    if (
        str(record.task_uuid) != str(instance.correlation_id)
        or record.target_execution_organization_id != instance.organization_id
        or record.target_execution_node_id != instance.node_id
    ):
        return
    product_task = (
        Task.objects.select_for_update()
        .filter(
            organization_id=record.organization_id,
            task_uuid=record.task_uuid,
        )
        .first()
    )
    if product_task is None:
        return
    # Cancellation owns the product Task lock before mutating items. Following
    # the same Task -> item lock order prevents a completion/cancel deadlock,
    # and a late Agent result must never reverse a user-visible cancellation.
    if product_task.status == Task.Status.CANCELLED:
        _project_cancelled_restore_item(
            record=record,
            item_id=item_id,
            node_task=instance,
            product_task=product_task,
        )
        return
    if product_task.status in TERMINAL_STATUSES and (
        instance.status not in _TERMINAL_NODE_STATUSES
        or item.terminal_projection_at is not None
    ):
        return
    if instance.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
        _ensure_product_task_running(product_task)
        from apps.restore.services.restore_progress import (
            maybe_trigger_restore_progress,
        )

        maybe_trigger_restore_progress(node_task=instance)
        _set_step_status(
            task=product_task,
            step_name="restore",
            status=TaskStep.Status.RUNNING,
            current_step="restore",
        )
        return
    if instance.status not in _TERMINAL_NODE_STATUSES:
        return
    if item_id:
        _sync_restore_item(
            record=record,
            item_id=item_id,
            node_task=instance,
            product_task=product_task,
        )
    if record.purpose == RestoreRecord.Purpose.LENS_WORKSPACE:
        remaining = RestoreRecordItem.objects.filter(
            restore_record=record,
            node_task_id__isnull=True,
            status=RestoreRecordItem.Status.PENDING,
        )
        if remaining.exists():
            if instance.status == NodeTask.Status.SUCCESS:
                transaction.on_commit(
                    lambda record_id=record.id: _continue_lens_workspace_restore(
                        record_id
                    )
                )
            else:
                remaining.update(
                    status=RestoreRecordItem.Status.SKIPPED,
                    error_code="RESTORE_PREVIOUS_ITEM_FAILED",
                    error_message=(
                        "A previous Chat workspace restore item did not complete."
                    ),
                    terminal_projection_at=timezone.now(),
                )
    from apps.restore.services.restore_progress import sync_restore_record_progress

    sync_restore_record_progress(record=record)
    _finalize_record_if_done(record=record, product_task=product_task)


def _handle_restore_repository_server_task(*, node_task: NodeTask) -> None:
    if node_task.status not in _TERMINAL_NODE_STATUSES:
        return
    if node_task.kind != "repository.server.start":
        return
    record = RestoreRecord.objects.filter(
        organization_id=node_task.organization_id,
        task_uuid=node_task.correlation_id,
    ).first()
    if record is None:
        return
    product_task = (
        Task.objects.select_for_update()
        .filter(
            organization_id=record.organization_id,
            task_uuid=record.task_uuid,
        )
        .first()
    )
    if product_task is None:
        return
    # The product task is authoritative. A repository-server start callback
    # can arrive synchronously while cancellation is sealing its NodeTask, or
    # late after another terminal outcome. It must not overwrite restore items
    # or append a contradictory repository failure after that boundary.
    if product_task.status in TERMINAL_STATUSES:
        return
    if node_task.status == NodeTask.Status.SUCCESS:
        from apps.restore.services.interface import _dispatch_restore_items

        _dispatch_restore_items(
            organization_id=record.organization_id,
            record=record,
            task=product_task,
        )
        return
    message = _repository_server_failure_message(node_task)[:2000]
    repository_id = (
        RestoreRecordItem.objects.filter(
            organization_id=record.organization_id,
            restore_record=record,
        )
        .values_list("repository_id", flat=True)
        .first()
    )
    repository_name = (
        Repository.objects.filter(
            organization_id=record.organization_id,
            id=repository_id,
        )
        .values_list("name", flat=True)
        .first()
        if repository_id
        else ""
    )
    RestoreRecordItem.objects.filter(
        organization_id=record.organization_id,
        restore_record=record,
        node_task_id__isnull=True,
        status__in=[RestoreRecordItem.Status.PENDING, RestoreRecordItem.Status.RUNNING],
    ).update(
        status=RestoreRecordItem.Status.FAILED,
        error_code="RESTORE_REPOSITORY_SERVER_FAILED",
        error_message=message,
    )
    if not TaskEvent.objects.filter(
        task=product_task,
        step__step_name="dispatch_agent",
        message="Restore repository server failed",
        metadata__node_task_id=str(node_task.id),
    ).exists():
        append_task_step_event(
            task=product_task,
            step_name="dispatch_agent",
            level=TaskEvent.Level.ERROR,
            message="Restore repository server failed",
            metadata={
                "node_task_id": str(node_task.id),
                "error_message": message,
                "object_name": repository_name,
            },
        )
    RestoreRecordItem.objects.filter(
        restore_record=record,
        terminal_projection_at__isnull=True,
        status__in=[
            RestoreRecordItem.Status.SUCCESS,
            RestoreRecordItem.Status.FAILED,
            RestoreRecordItem.Status.SKIPPED,
            RestoreRecordItem.Status.CANCELLED,
        ],
    ).update(terminal_projection_at=timezone.now())
    _finalize_record_if_done(record=record, product_task=product_task)


def _ensure_product_task_running(task: Task) -> None:
    if task.status == Task.Status.PENDING:
        start_task(task_uuid=task.task_uuid, organization_id=task.organization_id)


def _project_cancelled_restore_item(
    *,
    record: RestoreRecord,
    item_id: int,
    node_task: NodeTask,
    product_task: Task,
) -> None:
    """Complete a cancelled item projection without applying a late result."""
    if not item_id:
        return
    item = (
        RestoreRecordItem.objects.select_for_update()
        .filter(
            organization_id=record.organization_id,
            restore_record=record,
            id=item_id,
        )
        .first()
    )
    if item is None:
        return
    previous_status = item.status
    if item.status not in _TERMINAL_RESTORE_ITEM_STATUSES:
        message = str(
            product_task.error_message or item.error_message or "Restore stopped."
        ).strip()[:2000]
        item.status = RestoreRecordItem.Status.CANCELLED
        item.error_code = "TASK_CANCELLED"
        item.error_message = message
        item.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )
    append_restore_item_terminal_event(
        task=product_task,
        item=item,
        node_task_id=item.node_task_id or node_task.id,
        previous_status=previous_status,
    )
    if item.terminal_projection_at is None:
        item.terminal_projection_at = timezone.now()
        item.save(update_fields=["terminal_projection_at", "updated_at"])


def _sync_restore_item(
    *,
    record: RestoreRecord,
    item_id: int,
    node_task: NodeTask,
    product_task: Task,
) -> None:
    item = (
        RestoreRecordItem.objects.select_for_update()
        .filter(
            organization_id=record.organization_id,
            restore_record=record,
            id=item_id,
        )
        .first()
    )
    if item is None:
        return
    previous_status = item.status
    result_payload = node_task.result if isinstance(node_task.result, dict) else {}
    if node_task.status == NodeTask.Status.SUCCESS:
        item.status = (
            RestoreRecordItem.Status.SKIPPED
            if str(result_payload.get("restore_outcome") or "").strip().lower()
            == "skipped"
            else RestoreRecordItem.Status.SUCCESS
        )
        item.result_payload = result_payload
        item.error_code = ""
        item.error_message = ""
    elif node_task.status == NodeTask.Status.CANCELED:
        item.status = RestoreRecordItem.Status.CANCELLED
        item.result_payload = result_payload
        item.error_code = "TASK_CANCELLED"
        item.error_message = (node_task.last_error or "Restore stopped.").strip()[:2000]
    else:
        item.status = RestoreRecordItem.Status.FAILED
        item.result_payload = result_payload
        (
            item.error_code,
            item.error_message,
            remediation,
            diagnostic,
        ) = _restore_failure_details(
            node_task=node_task,
            item=item,
        )
        item.result_payload = {
            **result_payload,
            "error_code": item.error_code,
            "error_message": item.error_message,
        }
        if remediation:
            item.result_payload["error_remediation"] = remediation
        if diagnostic:
            item.result_payload["error_diagnostic"] = diagnostic
    item.save(
        update_fields=[
            "status",
            "result_payload",
            "error_code",
            "error_message",
            "updated_at",
        ]
    )
    append_restore_item_terminal_event(
        task=product_task,
        item=item,
        node_task_id=node_task.id,
        previous_status=previous_status,
    )
    if item.terminal_projection_at is None:
        item.terminal_projection_at = timezone.now()
        item.save(update_fields=["terminal_projection_at", "updated_at"])


def _restore_failure_details(
    *,
    node_task: NodeTask,
    item: RestoreRecordItem,
) -> tuple[str, str, str, str]:
    result = node_task.result if isinstance(node_task.result, dict) else {}
    raw_message = str(
        node_task.last_error
        or result.get("error_message")
        or node_task.status
        or "Restore agent task failed."
    ).strip()
    lower = raw_message.lower()
    structured_code = str(result.get("error_code") or "").strip()[:80]
    structured_message = str(result.get("error_message") or "").strip()[:2000]
    remediation = str(result.get("error_remediation") or "").strip()[:2000]
    diagnostic = str(result.get("error_diagnostic") or "").strip()[:2000]
    if structured_code == "RESTORE_TARGET_PERMISSION_DENIED":
        return (
            structured_code,
            structured_message or _restore_permission_message(item),
            remediation or _restore_permission_remediation(),
            diagnostic,
        )
    if _is_legacy_restore_target_permission_failure(
        raw_message=raw_message,
        item=item,
    ):
        return (
            "RESTORE_TARGET_PERMISSION_DENIED",
            _restore_permission_message(item),
            _restore_permission_remediation(),
            raw_message[:2000],
        )
    if "agent restarted before task completed" in lower:
        return "AGENT_RESTARTED", raw_message[:2000], remediation, diagnostic
    return (
        structured_code or "RESTORE_AGENT_FAILED",
        structured_message or raw_message[:2000],
        remediation,
        diagnostic,
    )


def _restore_permission_message(item: RestoreRecordItem) -> str:
    return f'Permission denied while writing restore target "{item.target_path}".'[:2000]


def _restore_permission_remediation() -> str:
    return (
        "Verify that the Agent service account can modify the existing target and "
        "write to its parent directory. Use Skip to preserve an existing target."
    )


def _is_legacy_restore_target_permission_failure(
    *,
    raw_message: str,
    item: RestoreRecordItem,
) -> bool:
    lower = raw_message.lower()
    if "permission denied" not in lower and "access denied" not in lower:
        return False
    target_path = str(item.target_path or "").strip().lower()
    if target_path and target_path in lower:
        return True
    return any(
        marker in lower
        for marker in (
            "error copying",
            "copy file",
            "error creating file",
            "creating file",
            "creating directory",
            "restore target",
        )
    )


@transaction.atomic
def _finalize_record_if_done(*, record: RestoreRecord, product_task: Task) -> None:
    # Serialize the final item callbacks and the periodic reconciler on the
    # product task.  This protects TaskEvent sequence allocation and ensures
    # repository cleanup is scheduled only once when several items finish at
    # the same time.
    product_task = Task.objects.select_for_update().get(pk=product_task.pk)
    if product_task.status in TERMINAL_STATUSES:
        return
    statuses = list(record.items.values_list("status", flat=True))
    if not statuses or any(
        status in {RestoreRecordItem.Status.PENDING, RestoreRecordItem.Status.RUNNING}
        for status in statuses
    ):
        return
    normalize_restore_task_type(record=record, task=product_task)
    from apps.restore.services.interface import stop_restore_repository_servers
    from apps.restore.services.direct_nas_mounts import release_for_record

    release_for_record(record=record)
    stop_restore_repository_servers(task=product_task)
    counts = _restore_item_counts(statuses)
    result_payload = {
        "restore_record_id": record.id,
        "item_count": len(statuses),
        **counts,
    }
    if counts["failed_item_count"] or counts["cancelled_item_count"]:
        error_message = _record_error_message(record)
        failed_directories = [
            {"path": item.target_path}
            for item in record.items.filter(status=RestoreRecordItem.Status.FAILED).only("target_path")
            if str(item.target_path or "").strip()
        ][:20]
        _set_step_status(
            task=product_task,
            step_name="restore",
            status=TaskStep.Status.FAILED,
            current_step="restore",
        )
        _set_step_status(
            task=product_task,
            step_name="finalize",
            status=TaskStep.Status.FAILED,
        )
        _append_finalize_event_once(
            task=product_task,
            record=record,
            level=TaskEvent.Level.ERROR,
            message="Restore finished with failed items",
            metadata={
                "backup_summary": {
                    "restore_record_id": record.restore_uid,
                    "failed_directories": failed_directories,
                },
            },
        )
        complete_task(
            task_uuid=product_task.task_uuid,
            organization_id=product_task.organization_id,
            status=Task.Status.FAILED,
            progress=product_task.progress,
            result_payload=result_payload,
            error_code="RESTORE_FAILED",
            error_message=error_message,
            include_error_details_in_event=False,
        )
        release_restore_usage(
            restore_record_id=record.id,
            snapshot_id=record.source_snapshot_id,
        )
        return
    _set_step_status(
        task=product_task,
        step_name="restore",
        status=TaskStep.Status.SUCCESS,
        progress=100,
        task_progress=RESTORE_FINALIZE_START,
        current_step="finalize",
    )
    _set_step_status(
        task=product_task,
        step_name="finalize",
        status=TaskStep.Status.SUCCESS,
        progress=100,
        task_progress=100,
        current_step="finalize",
    )
    if counts["skipped_item_count"]:
        _append_finalize_event_once(
            task=product_task,
            record=record,
            level=TaskEvent.Level.WARN,
            message="Restore finished with skipped items",
        )
    else:
        _append_finalize_event_once(
            task=product_task,
            record=record,
            message="Restore finished successfully",
        )
    complete_task(
        task_uuid=product_task.task_uuid,
        organization_id=product_task.organization_id,
        status=Task.Status.SUCCESS,
        progress=100,
        result_payload=result_payload,
    )
    release_restore_usage(
        restore_record_id=record.id,
        snapshot_id=record.source_snapshot_id,
    )


def _append_finalize_event_once(
    *,
    task: Task,
    record: RestoreRecord,
    message: str,
    level: str = TaskEvent.Level.INFO,
    metadata: dict[str, object] | None = None,
) -> None:
    if TaskEvent.objects.filter(
        task=task,
        step__step_name="finalize",
        message=message,
        metadata__restore_record_id=record.id,
    ).exists():
        return
    statuses = list(record.items.values_list("status", flat=True))
    event_metadata: dict[str, object] = {
        "restore_record_id": record.id,
        "item_count": len(statuses),
        **_restore_item_counts(statuses),
        "object_name": record.restore_uid,
    }
    event_metadata.update(metadata or {})
    append_task_step_event(
        task=task,
        step_name="finalize",
        level=level,
        message=message,
        metadata=event_metadata,
    )


def _restore_item_counts(statuses: list[str]) -> dict[str, int]:
    return {
        "restored_item_count": statuses.count(RestoreRecordItem.Status.SUCCESS),
        "skipped_item_count": statuses.count(RestoreRecordItem.Status.SKIPPED),
        "failed_item_count": statuses.count(RestoreRecordItem.Status.FAILED),
        "cancelled_item_count": statuses.count(RestoreRecordItem.Status.CANCELLED),
    }


def _payload_int(payload: Any, key: str) -> int:
    if not isinstance(payload, dict):
        return 0
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _record_error_message(record: RestoreRecord) -> str:
    item = record.items.exclude(error_message="").first()
    if item is not None:
        return item.error_message[:2000]
    return "One or more restore items failed."


def _set_step_status(
    *,
    task: Task,
    step_name: str,
    status: str,
    progress: int | float | None = None,
    task_progress: int | float | None = None,
    current_step: str | None = None,
) -> None:
    step = TaskStep.objects.filter(task=task, step_name=step_name).first()
    if step is not None:
        update_fields = ["status"]
        step.status = status
        if progress is not None:
            step.progress = progress
            update_fields.append("progress")
        step.save(update_fields=update_fields)
    task_updates: list[str] = []
    if current_step is not None:
        task.current_step = current_step
        task_updates.append("current_step")
    if task_progress is not None:
        task.progress = task_progress
        task_updates.append("progress")
    if task_updates:
        task_updates.append("updated_at")
        task.save(update_fields=task_updates)
