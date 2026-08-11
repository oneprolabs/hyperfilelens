from __future__ import annotations

import logging
from typing import Any

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.protection.models import BackupConfig, BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.protection.services.kopia_snapshot_delete import (
    classify_kopia_snapshot_delete_results,
    normalize_kopia_snapshot_id,
)
from apps.protection.services.backup_task import (
    _resolve_execution_target,
    _set_step_status,
)
from apps.protection.services.repository_compatibility import validate_backup_repository_compatible
from apps.protection.services.snapshot_repository_locator import (
    group_snapshot_directories_by_repository_locator,
    resolve_snapshot_repository_reader,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_access import repository_uses_bound_proxy
from apps.source.constants import PipelineStep
from apps.source.services.internal.selectable_ids import parse_selectable_id
from apps.source.services.internal.source_pipeline import force_set_pipeline_steps
from apps.task.models import Task, TaskEvent, TaskResource, TaskStep
from apps.task.services.interface import append_task_step_event, complete_task, create_task, start_task

logger = logging.getLogger(__name__)

_RESET_LOCK_TTL_SECONDS = 7200
_RESET_TERMINAL = {
    Task.Status.SUCCESS,
    Task.Status.FAILED,
    Task.Status.CANCELLED,
    Task.Status.TIMEOUT,
}


def _reset_lock_key(*, organization_id: int, source_type: str, source_ref_id: int) -> str:
    return f"protection:backup-config-reset:{organization_id}:{source_type}:{source_ref_id}"


def _source_key(source_type: str, source_ref_id: int) -> str:
    return f"{source_type}:{source_ref_id}"


def _source_from_id(source_id: str) -> tuple[str, int] | None:
    parsed = parse_selectable_id(source_id)
    if not parsed:
        return None
    source_type, source_ref_id = parsed
    if source_type not in {"agent", "nas"}:
        return None
    return source_type, int(source_ref_id)


def _active_reset_task(*, organization_id: int, source_type: str, source_ref_id: int) -> Task | None:
    tasks = (
        Task.objects.filter(
            organization_id=organization_id,
            task_type=Task.Type.BACKUP_CONFIG_RESET,
            resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
            resources__resource_subtype=source_type,
            resources__resource_id=source_ref_id,
        )
        .exclude(status__in=_RESET_TERMINAL)
        .order_by("-created_at", "-id")
        .distinct()
    )
    return tasks.first()


def _configs_for_source(*, organization_id: int, source_type: str, source_ref_id: int) -> list[BackupConfig]:
    return list(
        BackupConfig.objects.filter(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
        ).order_by("id")
    )


def _snapshots_for_configs(*, organization_id: int, config_ids: list[int]) -> list[BackupSourceSnapshot]:
    if not config_ids:
        return []
    return list(
        BackupSourceSnapshot.objects.filter(
            organization_id=organization_id,
            backup_config_id__in=config_ids,
        ).order_by("id")
    )


def _kopia_snapshot_ids(rows: list[BackupSourceSnapshotDirectory]) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for row in rows:
        snapshot_id = normalize_kopia_snapshot_id(row.kopia_snapshot_id)
        if not snapshot_id or snapshot_id in seen:
            continue
        seen.add(snapshot_id)
        ids.append(snapshot_id)
    return ids


def _snapshot_rows(source_snapshot: BackupSourceSnapshot) -> list[BackupSourceSnapshotDirectory]:
    return list(
        BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=source_snapshot,
        )
        .exclude(status=BackupSourceSnapshotDirectory.Status.DELETED)
        .order_by("id")
    )


def _resource_defs(
    *,
    source_type: str,
    source_ref_id: int,
    configs: list[BackupConfig],
    snapshots: list[BackupSourceSnapshot],
) -> list[dict[str, Any]]:
    return [
        {
            "resource_type": TaskResource.Type.BACKUP_SOURCE,
            "resource_subtype": source_type,
            "resource_id": source_ref_id,
            "is_primary": True,
        }
    ]


@transaction.atomic
def ensure_backup_config_reset_task(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    trigger_type: str = Task.TriggerType.MANUAL,
) -> tuple[Task | None, bool]:
    from apps.source.services.internal.source_operation_fence import (
        active_source_control_task,
        lock_source_identity,
    )

    lock_source_identity(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    active = _active_reset_task(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if active is not None:
        return active, False

    source_control_blocker = active_source_control_task(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if source_control_blocker is not None:
        raise ValidationError(
            {
                "source_ref_id": (
                    "A source deregistration task is already running. "
                    "Wait for it to finish before resetting configuration."
                ),
                "task_uuid": str(source_control_blocker.task_uuid),
            }
        )

    from apps.source.services.internal.backup_source_delete import (
        _running_tasks_for_source,
    )

    running = _running_tasks_for_source(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if running:
        raise ValidationError(
            {
                "source_ref_id": (
                    f"{len(running)} backup or restore task(s) are still running."
                )
            }
        )

    configs = _configs_for_source(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if not configs:
        return None, False
    config_ids = [config.id for config in configs]
    snapshots = _snapshots_for_configs(organization_id=organization_id, config_ids=config_ids)
    repository_ids = sorted({int(config.repository_id) for config in configs if int(config.repository_id or 0) > 0})
    payload = {
        "source_type": source_type,
        "source_ref_id": source_ref_id,
        "backup_config_ids": config_ids,
        "repository_ids": repository_ids,
        "source_snapshot_ids": [snapshot.id for snapshot in snapshots],
    }
    task = create_task(
        organization_id=organization_id,
        task_type=Task.Type.BACKUP_CONFIG_RESET,
        display_name=f"Reset backup configuration for {_source_key(source_type, source_ref_id)}",
        trigger_type=trigger_type,
        request_payload=payload,
        resources=_resource_defs(
            source_type=source_type,
            source_ref_id=source_ref_id,
            configs=configs,
            snapshots=snapshots,
        ),
        steps=[
            {"step_name": "prepare_reset"},
            {"step_name": "delete_kopia_snapshots"},
            {"step_name": "delete_snapshot_records"},
            {"step_name": "delete_restore_plans"},
            {"step_name": "delete_backup_configs"},
            {"step_name": "finalize_reset"},
        ],
    )
    BackupConfig.objects.filter(id__in=config_ids).update(
        status=BackupConfig.Status.RESETTING,
        reset_task_uuid=task.task_uuid,
    )
    return task, True


def create_backup_config_reset_task(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    trigger_type: str = Task.TriggerType.MANUAL,
) -> Task | None:
    task, _created = ensure_backup_config_reset_task(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        trigger_type=trigger_type,
    )
    return task


def queue_backup_config_reset_task(*, task: Task, source_type: str, source_ref_id: int) -> None:
    from apps.protection.tasks.backup_config_reset import execute_backup_config_reset_task

    execute_backup_config_reset_task.delay(
        organization_id=task.organization_id,
        task_uuid=str(task.task_uuid),
        source_type=source_type,
        source_ref_id=int(source_ref_id),
    )


def create_and_queue_backup_config_reset_task(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    trigger_type: str = Task.TriggerType.MANUAL,
) -> tuple[Task | None, bool]:
    task, created = ensure_backup_config_reset_task(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        trigger_type=trigger_type,
    )
    if task is None:
        return None, False
    should_queue = created or task.status == Task.Status.PENDING
    if should_queue:
        transaction.on_commit(
            lambda task_id=task.id, st=source_type, sr=source_ref_id: queue_backup_config_reset_task(
                task=Task.objects.get(id=task_id),
                source_type=st,
                source_ref_id=sr,
            )
        )
    return task, created


def _reset_block_message(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
) -> str:
    from apps.source.services.internal.backup_source_delete import (
        _active_unregister_task_for_source,
        _running_tasks_for_source,
    )

    if _active_unregister_task_for_source(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    ):
        return "A source deregistration task is already running."
    running = _running_tasks_for_source(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if running:
        return f"{len(running)} backup or restore task(s) are still running."
    return ""


def create_reset_tasks_for_sources(
    *,
    organization_id: int,
    source_ids: list[str],
    confirmation: str,
) -> dict[str, Any]:
    if confirmation != "RESET":
        raise ValidationError({"confirmation": "Type RESET exactly to confirm reset."})
    results: list[dict[str, Any]] = []
    for source_id in source_ids:
        parsed = _source_from_id(source_id)
        if parsed is None:
            results.append({"source_id": source_id, "status": "skipped", "message": "Invalid source id."})
            continue
        source_type, source_ref_id = parsed
        block_message = _reset_block_message(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
        )
        if block_message:
            results.append({
                "source_id": source_id,
                "source_type": source_type,
                "source_ref_id": source_ref_id,
                "status": "blocked",
                "message": block_message,
            })
            continue
        task, created = create_and_queue_backup_config_reset_task(
            organization_id=organization_id,
            source_type=source_type,
            source_ref_id=source_ref_id,
            trigger_type=Task.TriggerType.MANUAL,
        )
        if task is None:
            results.append({
                "source_id": source_id,
                "source_type": source_type,
                "source_ref_id": source_ref_id,
                "status": "skipped",
                "message": "No backup configuration found.",
            })
            continue
        payload = task.request_payload if isinstance(task.request_payload, dict) else {}
        results.append({
            "source_id": source_id,
            "source_type": source_type,
            "source_ref_id": source_ref_id,
            "status": task.status,
            "progress": task.progress,
            "task_id": task.id,
            "task_uuid": str(task.task_uuid),
            "created": created,
            "backup_config_ids": payload.get("backup_config_ids") or [],
        })
    created_count = len([item for item in results if item.get("created")])
    return {"created_count": created_count, "results": results}


def run_backup_config_reset_task(
    *,
    organization_id: int,
    task_uuid: str,
    source_type: str,
    source_ref_id: int,
) -> dict[str, Any]:
    lock_key = _reset_lock_key(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
    )
    if not cache.add(lock_key, "running", timeout=_RESET_LOCK_TTL_SECONDS):
        return {"task_uuid": str(task_uuid), "source_type": source_type, "source_ref_id": source_ref_id, "status": "locked"}
    try:
        return _run_backup_config_reset_task_locked(
            organization_id=organization_id,
            task_uuid=task_uuid,
            source_type=source_type,
            source_ref_id=source_ref_id,
        )
    finally:
        cache.delete(lock_key)


def _run_backup_config_reset_task_locked(
    *,
    organization_id: int,
    task_uuid: str,
    source_type: str,
    source_ref_id: int,
) -> dict[str, Any]:
    task = Task.objects.filter(organization_id=organization_id, task_uuid=task_uuid).first()
    if task is None:
        raise Task.DoesNotExist
    if task.status in _RESET_TERMINAL:
        return task.result_payload if isinstance(task.result_payload, dict) else {}
    if task.status == Task.Status.PENDING:
        task = start_task(task_uuid=task.task_uuid, organization_id=organization_id)

    payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    config_ids = [int(value) for value in payload.get("backup_config_ids") or [] if int(value or 0) > 0]

    try:
        _set_step_status(
            task=task,
            step_name="prepare_reset",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=10,
            current_step="delete_kopia_snapshots",
        )
        deleted_physical_count, failed_physical_count = _delete_physical_snapshots(
            task=task,
            organization_id=organization_id,
            config_ids=config_ids,
        )
        _set_step_status(
            task=task,
            step_name="delete_snapshot_records",
            status=TaskStep.Status.RUNNING,
            progress=20,
            task_progress=70,
            current_step="delete_snapshot_records",
        )
        snapshot_record_qs = BackupSourceSnapshot.objects.filter(
            organization_id=organization_id,
            backup_config_id__in=config_ids,
        )
        snapshot_records_removed = snapshot_record_qs.count()
        snapshot_record_qs.delete()
        _set_step_status(
            task=task,
            step_name="delete_snapshot_records",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=78,
        )

        from apps.restore.models import RestorePlan

        _set_step_status(
            task=task,
            step_name="delete_restore_plans",
            status=TaskStep.Status.RUNNING,
            progress=20,
            task_progress=82,
            current_step="delete_restore_plans",
        )
        restore_plans_removed = RestorePlan.objects.filter(
            organization_id=organization_id,
            backup_config_id__in=config_ids,
        ).delete()[0]
        _set_step_status(
            task=task,
            step_name="delete_restore_plans",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=88,
        )

        _set_step_status(
            task=task,
            step_name="delete_backup_configs",
            status=TaskStep.Status.RUNNING,
            progress=20,
            task_progress=92,
            current_step="delete_backup_configs",
        )
        backup_config_qs = BackupConfig.objects.filter(
            organization_id=organization_id,
            id__in=config_ids,
        )
        backup_configs_removed = backup_config_qs.count()
        backup_config_qs.delete()
        _set_step_status(
            task=task,
            step_name="delete_backup_configs",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=96,
        )

        force_set_pipeline_steps(
            organization_id=organization_id,
            ids=[_source_key(source_type, source_ref_id)],
            step=PipelineStep.CONFIG,
            operation_task_uuid=str(task.task_uuid),
        )
        _set_step_status(
            task=task,
            step_name="finalize_reset",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=100,
            current_step="finalize_reset",
        )
        result = {
            "source_type": source_type,
            "source_ref_id": source_ref_id,
            "backup_config_ids": config_ids,
            "deleted_physical_snapshot_count": deleted_physical_count,
            "failed_physical_snapshot_count": failed_physical_count,
            "snapshot_records_removed": snapshot_records_removed,
            "restore_plans_removed": restore_plans_removed,
            "backup_configs_removed": backup_configs_removed,
        }
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=organization_id,
            status=Task.Status.SUCCESS,
            progress=100,
            result_payload=result,
        )
        return result
    except Exception as exc:
        logger.exception(
            "backup config reset failed org_id=%s source=%s:%s task_uuid=%s",
            organization_id,
            source_type,
            source_ref_id,
            task_uuid,
        )
        BackupConfig.objects.filter(
            organization_id=organization_id,
            id__in=config_ids,
        ).update(
            status=BackupConfig.Status.RESET_FAILED,
            reset_task_uuid=task.task_uuid,
        )
        message = str(exc)[:2000] or "Backup configuration reset failed."
        append_task_step_event(
            task=task,
            step_name=task.current_step or "delete_kopia_snapshots",
            level=TaskEvent.Level.ERROR,
            message="Backup configuration reset failed",
            metadata={"error_message": message},
        )
        _fail_open_steps(task, failed_step=task.current_step or "delete_kopia_snapshots")
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=organization_id,
            status=Task.Status.FAILED,
            progress=task.progress,
            error_code="BACKUP_CONFIG_RESET_FAILED",
            error_message=message,
        )
        return {"source_type": source_type, "source_ref_id": source_ref_id, "status": "failed", "error_message": message}


def _delete_physical_snapshots(*, task: Task, organization_id: int, config_ids: list[int]) -> tuple[int, int]:
    snapshots = _snapshots_for_configs(organization_id=organization_id, config_ids=config_ids)
    total = len(snapshots)
    if not snapshots:
        _set_step_status(
            task=task,
            step_name="delete_kopia_snapshots",
            status=TaskStep.Status.SKIPPED,
            progress=100,
            task_progress=68,
        )
        return 0, 0

    deleted_count = 0
    failed_count = 0
    _set_step_status(
        task=task,
        step_name="delete_kopia_snapshots",
        status=TaskStep.Status.RUNNING,
        progress=0,
        task_progress=12,
        current_step="delete_kopia_snapshots",
    )
    for index, snapshot in enumerate(snapshots, start=1):
        rows = _snapshot_rows(snapshot)
        kopia_ids = _kopia_snapshot_ids(rows)
        if not kopia_ids:
            deleted_count += 0
            _mark_snapshot_rows_deleted(snapshot)
            _update_delete_progress(task=task, index=index, total=total)
            continue
        append_task_step_event(
            task=task,
            step_name="delete_kopia_snapshots",
            message="Deleting Kopia snapshots for reset",
            metadata={
                "source_snapshot_id": snapshot.id,
                "snapshot_uid": snapshot.snapshot_uid,
                "kopia_snapshot_ids": kopia_ids,
            },
        )
        repository = validate_backup_repository_compatible(
            organization_id=organization_id,
            source_type=snapshot.source_type,
            source_ref_id=snapshot.source_ref_id,
            repository_id=snapshot.repository_id,
        )
        physical_rows = [
            row
            for row in rows
            if normalize_kopia_snapshot_id(row.kopia_snapshot_id)
        ]
        groups = group_snapshot_directories_by_repository_locator(
            directories=physical_rows,
            repository=repository,
        )
        for group in groups:
            group_kopia_ids = _kopia_snapshot_ids(group)
            outcome = _run_kopia_snapshot_delete(
                organization_id=organization_id,
                task=task,
                source_snapshot=snapshot,
                directory=group[0],
                repository=repository,
                kopia_ids=group_kopia_ids,
            )
            result = outcome.result if isinstance(outcome.result, dict) else {}
            item_results = (
                result.get("results")
                if isinstance(result.get("results"), list)
                else []
            )
            deleted_ids, already_absent_ids, hard_failures = (
                classify_kopia_snapshot_delete_results(
                    [item for item in item_results if isinstance(item, dict)],
                )
            )
            reconciled_ids = deleted_ids | already_absent_ids
            if already_absent_ids:
                append_task_step_event(
                    task=task,
                    step_name="delete_kopia_snapshots",
                    level=TaskEvent.Level.WARN,
                    message="Kopia snapshot already absent; continuing reset cleanup",
                    metadata={
                        "source_snapshot_id": snapshot.id,
                        "kopia_snapshot_ids": sorted(already_absent_ids),
                    },
                )
            if reconciled_ids:
                BackupSourceSnapshotDirectory.objects.filter(
                    source_snapshot=snapshot,
                    id__in=[row.id for row in group],
                    kopia_snapshot_id__in=list(reconciled_ids),
                ).update(status=BackupSourceSnapshotDirectory.Status.DELETED)
            if outcome.timed_out or hard_failures:
                failed_count += max(
                    len(hard_failures),
                    1 if outcome.timed_out else 0,
                )
                detail = str(
                    getattr(outcome.task, "last_error", "") or ""
                ).strip()
                if not detail and hard_failures:
                    first = hard_failures[0]
                    detail = str(first.get("error_message") or "")
                    delete = first.get("delete")
                    if isinstance(delete, dict):
                        detail = str(
                            delete.get("stderr_tail")
                            or delete.get("stderr")
                            or detail
                        )
                raise ValidationError(
                    {
                        "detail": detail
                        or "One or more Kopia snapshots failed to delete."
                    }
                )
            if not outcome.ok and not reconciled_ids:
                failed_count += int(result.get("failed_count") or 1)
                raise ValidationError(
                    {
                        "detail": str(
                            getattr(outcome.task, "last_error", "")
                            or "One or more Kopia snapshots failed to delete."
                        )
                    }
                )
            deleted_count += len(reconciled_ids)
        _mark_snapshot_rows_deleted(snapshot)
        _update_delete_progress(task=task, index=index, total=total)

    _set_step_status(
        task=task,
        step_name="delete_kopia_snapshots",
        status=TaskStep.Status.SUCCESS,
        progress=100,
        task_progress=68,
    )
    return deleted_count, failed_count


def _run_kopia_snapshot_delete(
    *,
    organization_id: int,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory: BackupSourceSnapshotDirectory,
    repository: Repository,
    kopia_ids: list[str],
):
    from apps.node.services.interface import run_agent_task_sync

    fallback_target = None
    if not repository_uses_bound_proxy(repository):
        fallback_target = _resolve_execution_target(source_snapshot=source_snapshot)
    repository_access = resolve_snapshot_repository_reader(
        directory=directory,
        repository=repository,
        fallback_node=fallback_target.node if fallback_target is not None else None,
        source_type=source_snapshot.source_type,
        source_ref_id=source_snapshot.source_ref_id,
    )
    return run_agent_task_sync(
        organization_id=organization_id,
        node_id=repository_access.node.id,
        kind="snapshot.delete",
        payload={
            "repository": repository_access.repository_payload,
            "kopia_snapshot_ids": kopia_ids,
        },
        correlation_type="protection.backup_config_reset",
        correlation_id=str(task.task_uuid),
        wait_timeout_seconds=3600,
    )


def _mark_snapshot_rows_deleted(snapshot: BackupSourceSnapshot) -> None:
    BackupSourceSnapshotDirectory.objects.filter(source_snapshot=snapshot).exclude(
        status=BackupSourceSnapshotDirectory.Status.DELETED
    ).update(status=BackupSourceSnapshotDirectory.Status.DELETED)


def _update_delete_progress(*, task: Task, index: int, total: int) -> None:
    step_progress = int((index / max(total, 1)) * 100)
    task_progress = 12 + int((index / max(total, 1)) * 56)
    _set_step_status(
        task=task,
        step_name="delete_kopia_snapshots",
        status=TaskStep.Status.RUNNING,
        progress=step_progress,
        task_progress=task_progress,
    )


def _fail_open_steps(task: Task, *, failed_step: str) -> None:
    for step in task.steps.all():
        if step.step_name == failed_step:
            step.status = TaskStep.Status.FAILED
            step.save(update_fields=["status"])
            continue
        if step.status in {TaskStep.Status.SUCCESS, TaskStep.Status.FAILED, TaskStep.Status.SKIPPED}:
            continue
        step.status = TaskStep.Status.SKIPPED
        step.save(update_fields=["status"])


def reconcile_stuck_backup_config_reset_tasks(
    *,
    limit: int = 50,
    stale_seconds: int = 90,
) -> dict[str, int]:
    """Re-dispatch backup-config-reset Celery jobs left PENDING after dispatch never ran."""
    from datetime import timedelta

    from apps.protection.tasks.backup_config_reset import execute_backup_config_reset_task

    cutoff = timezone.now() - timedelta(seconds=max(30, int(stale_seconds)))
    stuck = list(
        Task.objects.filter(
            task_type=Task.Type.BACKUP_CONFIG_RESET,
            status=Task.Status.PENDING,
            updated_at__lt=cutoff,
        )
        .order_by("updated_at", "id")[: max(1, int(limit))]
    )
    redispatched = 0
    for row in stuck:
        payload = row.request_payload if isinstance(row.request_payload, dict) else {}
        source_type = str(payload.get("source_type") or "").strip()
        source_ref_id = int(payload.get("source_ref_id") or 0)
        if not source_type or source_ref_id <= 0:
            continue
        execute_backup_config_reset_task.delay(
            organization_id=int(row.organization_id),
            task_uuid=str(row.task_uuid),
            source_type=source_type,
            source_ref_id=source_ref_id,
        )
        redispatched += 1
        logger.warning(
            "re-dispatched stuck backup_config_reset task id=%s uuid=%s updated_at=%s",
            row.id,
            row.task_uuid,
            row.updated_at,
        )
    return {"scanned": len(stuck), "redispatched": redispatched}
