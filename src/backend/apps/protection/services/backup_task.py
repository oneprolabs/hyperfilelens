from __future__ import annotations

import ntpath
import posixpath
import logging
import re
import threading
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections
from django.db import transaction
from django.utils import timezone

from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.interface import (
    AgentTaskSyncResult,
    deliver_agent_task,
    run_agent_task_sync,
    wait_for_agent_task,
)
from apps.protection import conf as protection_conf
from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.protection.services.backup_source_snapshot import (
    create_source_snapshot,
    mark_source_snapshot_failed,
)
from apps.protection.services.progress.orchestrated_progress import (
    BACKUP_TRANSFER_END,
    BACKUP_TRANSFER_END as _KOPIA_TASK_END,
    BACKUP_TRANSFER_START,
    orchestrated_backup_from_agent_progress,
)
from apps.protection.services.repository_compatibility import validate_backup_repository_compatible
from apps.protection.services.source_execution import (
    ExecutionTarget,
    resolve_source_execution_target,
)
from apps.source.constants import ResourceType
from apps.source.models import SourceResource
from apps.storage.repositories.models import Repository
from apps.source.services.internal.selectable_ids import parse_selectable_id
from apps.storage.services.internal.repository_secrets import build_repository_runtime_payload
from apps.task.models import Task, TaskResource, TaskStep
from apps.task.services.interface import append_task_step_event, complete_task, create_task

_ACTIVE_TASK_STATUSES = {Task.Status.PENDING, Task.Status.RUNNING}
_TASK_TRIGGER_MAP = {
    BackupSourceSnapshot.TriggerType.MANUAL: Task.TriggerType.MANUAL,
    BackupSourceSnapshot.TriggerType.SCHEDULE: Task.TriggerType.SYSTEM,
    BackupSourceSnapshot.TriggerType.RETRY: Task.TriggerType.MANUAL,
    BackupSourceSnapshot.TriggerType.API: Task.TriggerType.MANUAL,
}
_DEFAULT_DIRECTORY_WAIT_TIMEOUT_SECONDS = 3600
_BACKUP_EXECUTION_LOCK_TTL_BUFFER_SECONDS = 600
_BACKUP_EXECUTION_LOCK_VALUE = "running"
_TASK_TERMINAL_STATUSES = {
    Task.Status.SUCCESS,
    Task.Status.FAILED,
    Task.Status.CANCELLED,
    Task.Status.TIMEOUT,
}
_NODE_TASK_TERMINAL_STATUSES = {
    NodeTask.Status.SUCCESS,
    NodeTask.Status.FAILED,
    NodeTask.Status.TIMEOUT,
    NodeTask.Status.CANCELED,
}
_DIRECTORY_TERMINAL_STATUSES = {
    BackupSourceSnapshotDirectory.Status.AVAILABLE,
    BackupSourceSnapshotDirectory.Status.FAILED,
    BackupSourceSnapshotDirectory.Status.DELETED,
}

logger = logging.getLogger(__name__)

_KOPIA_TASK_SPAN = _KOPIA_TASK_END - BACKUP_TRANSFER_START

_AGENT_PHASE_KOPIA_PERCENT = {
    "started": 1.0,
    "repository_prepare": 3.0,
    "repository_ready": 8.0,
    "snapshot_start": 10.0,
    "kopia_snapshot": 10.0,
    "hashing": 15.0,
    "uploading": 50.0,
    "running": 12.0,
    "snapshot_created": 100.0,
}


@dataclass(frozen=True)
class RequestedBackupSource:
    source_type: str
    source_ref_id: int


def _task_progress(value: int | float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _progress_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _kopia_percent_from_agent_progress(progress: dict[str, Any]) -> float | None:
    for key in ("kopia_percent", "percent"):
        parsed = _progress_number(progress.get(key))
        if parsed is not None:
            return max(0.0, min(100.0, parsed))
    for key in ("kopia_phase", "phase"):
        phase = str(progress.get(key) or "").strip().lower()
        if phase in _AGENT_PHASE_KOPIA_PERCENT:
            return _AGENT_PHASE_KOPIA_PERCENT[phase]
    return None


def _backup_progress_for_directory(
    *,
    directory_index: int,
    total_dirs: int,
    kopia_percent: float,
) -> tuple[float, float]:
    _ = directory_index, total_dirs
    step_progress = max(0.0, min(100.0, kopia_percent))
    task_progress = min(
        BACKUP_TRANSFER_END - 0.01,
        BACKUP_TRANSFER_START + (step_progress / 100.0) * _KOPIA_TASK_SPAN,
    )
    return step_progress, task_progress


def _backup_success_progress(successful_directory_count: int, total_dirs: int) -> tuple[float, float]:
    total = max(total_dirs, 1)
    successful = max(0, min(successful_directory_count, total))
    success_ratio = successful / total
    step_progress = success_ratio * 100.0
    task_progress = BACKUP_TRANSFER_START + success_ratio * _KOPIA_TASK_SPAN
    return step_progress, task_progress


def _apply_backup_agent_progress(
    *,
    task: Task,
    directory_index: int,
    total_dirs: int,
    progress: dict[str, Any],
    last_applied_percent: dict[str, float],
) -> None:
    _ = directory_index, total_dirs, last_applied_percent
    task_progress = orchestrated_backup_from_agent_progress(task=task, progress=progress)
    if task_progress is None:
        return
    kopia_percent = _kopia_percent_from_agent_progress(progress)
    step_progress = kopia_percent if kopia_percent is not None else 0.0
    _set_step_status(
        task=task,
        step_name="kopia_snapshot",
        status=TaskStep.Status.RUNNING,
        progress=step_progress,
        task_progress=task_progress,
    )


def _directory_wait_timeout_seconds() -> int:
    raw_value = getattr(
        settings,
        "PROTECTION_BACKUP_DIRECTORY_TIMEOUT_SECONDS",
        _DEFAULT_DIRECTORY_WAIT_TIMEOUT_SECONDS,
    )
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return _DEFAULT_DIRECTORY_WAIT_TIMEOUT_SECONDS


def _step(task: Task, step_name: str) -> TaskStep | None:
    return TaskStep.objects.filter(task=task, step_name=step_name).first()


def _set_step_status(
    *,
    task: Task,
    step_name: str,
    status: str,
    progress: int | float | Decimal | None = None,
    task_progress: int | float | Decimal | None = None,
    current_step: str | None = None,
) -> None:
    step = _step(task, step_name)
    if step is not None:
        update_fields = ["status"]
        step.status = status
        if progress is not None:
            step.progress = _task_progress(progress)
            update_fields.append("progress")
        step.save(update_fields=update_fields)
    task_updates: list[str] = []
    if current_step is not None:
        task.current_step = current_step
        task_updates.append("current_step")
    if task_progress is not None:
        current = float(task.progress or 0)
        next_progress = float(task_progress)
        if next_progress > current:
            task.progress = _task_progress(next_progress)
            task_updates.append("progress")
    if task_updates:
        task_updates.append("updated_at")
        task.save(update_fields=task_updates)


def _finalize_remaining_steps(task: Task, *, failed_step: str | None = None) -> None:
    for step in task.steps.all():
        if step.step_name == failed_step:
            continue
        if step.status in {TaskStep.Status.SUCCESS, TaskStep.Status.FAILED, TaskStep.Status.SKIPPED}:
            continue
        step.status = TaskStep.Status.SKIPPED
        step.progress = _task_progress(0)
        step.save(update_fields=["status", "progress"])


def _is_windows_path(path: str) -> bool:
    return "\\" in path or (len(path) >= 2 and path[1] == ":")


def _clean_path(path: str) -> str:
    value = str(path or "").strip()
    if not value:
        return ""
    if _is_windows_path(value):
        return ntpath.normpath(value)
    return posixpath.normpath(value)


def _is_subpath(root: str, path: str) -> bool:
    clean_root = _clean_path(root)
    clean_path = _clean_path(path)
    if not clean_root or not clean_path:
        return False
    if _is_windows_path(clean_root) or _is_windows_path(clean_path):
        root_cmp = ntpath.normcase(clean_root.rstrip("\\/"))
        path_cmp = ntpath.normcase(clean_path.rstrip("\\/"))
        return path_cmp == root_cmp or path_cmp.startswith(root_cmp + "\\")
    root_cmp = clean_root.rstrip("/") or "/"
    path_cmp = clean_path.rstrip("/") or "/"
    return path_cmp == root_cmp or path_cmp.startswith(root_cmp + "/")


def _source_display_name(*, organization_id: int, source_type: str, source_ref_id: int) -> str:
    if source_type == "agent":
        node = Node.objects.filter(
            organization_id=organization_id,
            id=source_ref_id,
            role=NodeRole.AGENT,
            is_deleted=False,
        ).first()
        return str(node.name if node else f"agent:{source_ref_id}")
    resource = SourceResource.objects.filter(
        organization_id=organization_id,
        id=source_ref_id,
        resource_type=ResourceType.NAS,
        is_deleted=False,
    ).first()
    return str(resource.name if resource else f"nas:{source_ref_id}")


def _normalize_sources(
    *,
    sources: list[dict[str, Any]] | None = None,
    source_ids: list[str] | None = None,
) -> list[RequestedBackupSource]:
    deduped: dict[tuple[str, int], RequestedBackupSource] = {}
    for item in sources or []:
        source_type = str(item.get("source_type") or "").strip().lower()
        if source_type not in {"agent", "nas"}:
            raise ValidationError({"sources": "source_type must be agent or nas."})
        try:
            source_ref_id = int(item.get("source_ref_id") or 0)
        except (TypeError, ValueError) as exc:
            raise ValidationError({"sources": "source_ref_id must be a positive integer."}) from exc
        if source_ref_id <= 0:
            raise ValidationError({"sources": "source_ref_id must be a positive integer."})
        key = (source_type, source_ref_id)
        deduped[key] = RequestedBackupSource(source_type=source_type, source_ref_id=source_ref_id)
    for source_id in source_ids or []:
        parsed = parse_selectable_id(str(source_id))
        if parsed is None or parsed[0] not in {"agent", "nas"}:
            raise ValidationError({"source_ids": f"Invalid source id: {source_id}"})
        key = (parsed[0], parsed[1])
        deduped[key] = RequestedBackupSource(source_type=parsed[0], source_ref_id=parsed[1])
    if not deduped:
        raise ValidationError({"sources": "At least one backup source is required."})
    return list(deduped.values())


def _backup_config_sources(
    *,
    organization_id: int,
    backup_config_ids: list[int] | None,
) -> list[RequestedBackupSource]:
    if not backup_config_ids:
        return []
    configs = BackupConfig.objects.filter(
        organization_id=organization_id,
        id__in=backup_config_ids,
    ).order_by("id")
    deduped: dict[tuple[str, int], RequestedBackupSource] = {}
    for config in configs:
        key = (config.source_type, config.source_ref_id)
        deduped[key] = RequestedBackupSource(
            source_type=config.source_type,
            source_ref_id=config.source_ref_id,
        )
    return list(deduped.values())


def _validate_selected_sources(*, organization_id: int, sources: list[RequestedBackupSource]) -> None:
    for source in sources:
        if source.source_type == "agent":
            exists = Node.objects.filter(
                organization_id=organization_id,
                id=source.source_ref_id,
                role=NodeRole.AGENT,
                is_deleted=False,
            ).exists()
        else:
            exists = SourceResource.objects.filter(
                organization_id=organization_id,
                id=source.source_ref_id,
                resource_type=ResourceType.NAS,
                is_deleted=False,
            ).exists()
        if not exists:
            raise ValidationError({"sources": f"Backup source not found: {source.source_type}:{source.source_ref_id}"})


def _load_backup_configs(
    *,
    organization_id: int,
    sources: list[RequestedBackupSource],
    backup_config_ids: list[int] | None = None,
) -> dict[tuple[str, int], list[BackupConfig]]:
    queryset = BackupConfig.objects.filter(organization_id=organization_id).prefetch_related("directories")
    if backup_config_ids:
        queryset = queryset.filter(id__in=backup_config_ids)
    configs = list(queryset.order_by("id"))
    config_map = {
        (config.source_type, config.source_ref_id): []
        for config in configs
    }
    for config in configs:
        config_map.setdefault((config.source_type, config.source_ref_id), []).append(config)

    if backup_config_ids:
        found_ids = {config.id for config in configs}
        missing_ids = sorted({int(value) for value in backup_config_ids} - found_ids)
        if missing_ids:
            raise ValidationError({"backup_config_ids": f"Backup configs not found: {', '.join(str(v) for v in missing_ids)}"})
        selected_sources = {(source.source_type, source.source_ref_id) for source in sources}
        invalid = [
            config.id
            for config in configs
            if (config.source_type, config.source_ref_id) not in selected_sources
        ]
        if invalid:
            raise ValidationError(
                {"backup_config_ids": f"Backup configs do not belong to the selected sources: {', '.join(str(v) for v in invalid)}"}
            )
    return config_map


def find_active_backup_task(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    backup_config_id: int,
) -> Task | None:
    active_tasks = Task.objects.filter(
        organization_id=organization_id,
        task_type=Task.Type.BACKUP,
        status__in=_ACTIVE_TASK_STATUSES,
    ).order_by("-created_at", "-id")
    for task in active_tasks:
        payload = task.request_payload if isinstance(task.request_payload, dict) else {}
        if (
            str(payload.get("source_type") or "") == source_type
            and int(payload.get("source_ref_id") or 0) == source_ref_id
            and int(payload.get("backup_config_id") or 0) == backup_config_id
        ):
            return task
    return None


def _existing_snapshot_by_idempotency(
    *,
    organization_id: int,
    idempotency_key: str,
) -> BackupSourceSnapshot | None:
    return BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        idempotency_key=str(idempotency_key).strip(),
    ).first()


def _backup_execution_lock_key(*, organization_id: int, task_uuid: str) -> str:
    return f"protection:backup:run:{organization_id}:{task_uuid}"


def _acquire_backup_execution_lock(*, organization_id: int, task_uuid: str) -> bool:
    # Short lock — orchestrator ticks are idempotent; prevents duplicate concurrent advances.
    ttl = max(120, int(protection_conf.PROTECTION_BACKUP_RECONCILE_INTERVAL_SECONDS) * 4)
    return bool(
        cache.add(
            _backup_execution_lock_key(
                organization_id=organization_id,
                task_uuid=task_uuid,
            ),
            _BACKUP_EXECUTION_LOCK_VALUE,
            timeout=ttl,
        )
    )


def _release_backup_execution_lock(*, organization_id: int, task_uuid: str) -> None:
    cache.delete(
        _backup_execution_lock_key(
            organization_id=organization_id,
            task_uuid=task_uuid,
        )
    )


def _queue_backup_execution(
    *,
    organization_id: int,
    task_uuid: str,
    source_snapshot_id: int,
) -> None:
    execution_backend = str(
        getattr(settings, "PROTECTION_BACKUP_EXECUTION_BACKEND", "celery")
        or "celery"
    ).strip().lower()
    if execution_backend != "celery":
        thread = threading.Thread(
            target=_run_backup_task_in_thread,
            kwargs={
                "organization_id": organization_id,
                "task_uuid": task_uuid,
                "source_snapshot_id": source_snapshot_id,
            },
            name=f"protection-backup-{task_uuid}",
            daemon=True,
        )
        thread.start()
        return

    from apps.protection.tasks.backup import execute_backup_source_task

    try:
        execute_backup_source_task.delay(
            organization_id=organization_id,
            task_uuid=task_uuid,
            source_snapshot_id=source_snapshot_id,
        )
    except Exception as exc:
        task = Task.objects.filter(
            organization_id=organization_id,
            task_uuid=task_uuid,
        ).first()
        snapshot = BackupSourceSnapshot.objects.filter(
            organization_id=organization_id,
            id=source_snapshot_id,
        ).first()
        message = f"Failed to queue backup execution: {exc}"
        if snapshot is not None:
            mark_source_snapshot_failed(
                source_snapshot=snapshot,
                error_code="BACKUP_QUEUE_FAILED",
                error_message=message,
            )
        if task is not None:
            complete_task(
                task_uuid=task.task_uuid,
                organization_id=organization_id,
                status=Task.Status.FAILED,
                progress=0,
                result_payload={
                    "source_snapshot_id": source_snapshot_id,
                    "source_snapshot_status": BackupSourceSnapshot.Status.FAILED,
                },
                error_code="BACKUP_QUEUE_FAILED",
                error_message=message,
            )


def _run_backup_task_in_thread(
    *,
    organization_id: int,
    task_uuid: str,
    source_snapshot_id: int,
) -> None:
    close_old_connections()
    try:
        run_backup_task(
            organization_id=organization_id,
            task_uuid=task_uuid,
            source_snapshot_id=source_snapshot_id,
        )
    except Exception:
        logger.exception(
            "Backup task execution failed in local background thread: task_uuid=%s source_snapshot_id=%s",
            task_uuid,
            source_snapshot_id,
        )
    finally:
        close_old_connections()


def start_backup_tasks(
    *,
    organization_id: int,
    sources: list[dict[str, Any]] | None = None,
    source_ids: list[str] | None = None,
    backup_config_ids: list[int] | None = None,
    trigger_type: str = BackupSourceSnapshot.TriggerType.MANUAL,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    requested_sources = _normalize_sources(sources=sources, source_ids=source_ids) if sources or source_ids else []
    if not requested_sources:
        requested_sources = _backup_config_sources(
            organization_id=organization_id,
            backup_config_ids=backup_config_ids,
        )
    _validate_selected_sources(organization_id=organization_id, sources=requested_sources)
    config_map = _load_backup_configs(
        organization_id=organization_id,
        sources=requested_sources,
        backup_config_ids=backup_config_ids,
    )

    batch_key = str(idempotency_key or timezone.now().strftime("%Y%m%d%H%M%S%f")).strip()
    results: list[dict[str, Any]] = []
    created_count = 0
    skipped_count = 0

    for source in requested_sources:
        configs = config_map.get((source.source_type, source.source_ref_id), [])
        if not configs:
            skipped_count += 1
            results.append(
                {
                    "source_type": source.source_type,
                    "source_ref_id": source.source_ref_id,
                    "backup_config_id": None,
                    "task_id": None,
                    "task_uuid": None,
                    "source_snapshot_id": None,
                    "source_snapshot_status": None,
                    "status": "skipped",
                    "message": "No backup config is bound to the selected source.",
                }
            )
            continue

        source_name = _source_display_name(
            organization_id=organization_id,
            source_type=source.source_type,
            source_ref_id=source.source_ref_id,
        )
        for config in configs:
            try:
                with transaction.atomic():
                    from apps.source.services.internal.source_operation_fence import (
                        assert_source_product_operation_allowed,
                    )

                    assert_source_product_operation_allowed(
                        organization_id=organization_id,
                        source_type=source.source_type,
                        source_ref_id=source.source_ref_id,
                    )
                validate_backup_repository_compatible(
                    organization_id=organization_id,
                    source_type=source.source_type,
                    source_ref_id=source.source_ref_id,
                    repository_id=config.repository_id,
                )
                repository = Repository.objects.get(
                    organization_id=organization_id,
                    id=config.repository_id,
                )
                if (
                    repository.repo_type == Repository.Type.NAS
                    and not repository.bind_node_type
                    and not repository.bind_node_id
                ):
                    from apps.protection.services.backup_config import (
                        ensure_direct_nas_repository_for_backup,
                    )

                    ensure_direct_nas_repository_for_backup(
                        organization_id=organization_id,
                        source_type=source.source_type,
                        source_ref_id=source.source_ref_id,
                        repository_id=repository.id,
                    )
                # Directory size is refreshed asynchronously for every new
                # backup because configured path contents can change without a
                # config edit. Never block the Backup Now request on path.size.
            except ValidationError as exc:
                skipped_count += 1
                results.append(
                    {
                        "source_type": source.source_type,
                        "source_ref_id": source.source_ref_id,
                        "backup_config_id": config.id,
                        "task_id": None,
                        "task_uuid": None,
                        "source_snapshot_id": None,
                        "source_snapshot_status": None,
                        "status": "failed",
                        "message": _validation_message(exc),
                    }
                )
                continue

            item_idempotency_key = f"{batch_key}:{source.source_type}:{source.source_ref_id}:{config.id}"
            task_display_name = f"Backup {source_name}"
            task_trigger_type = _TASK_TRIGGER_MAP.get(
                str(trigger_type).strip().lower(),
                Task.TriggerType.MANUAL,
            )
            snapshot_trigger_type = (
                BackupSourceSnapshot.TriggerType.SCHEDULE
                if task_trigger_type == Task.TriggerType.SYSTEM
                else BackupSourceSnapshot.TriggerType.MANUAL
            )
            existing_result: dict[str, Any] | None = None
            task: Task | None = None
            snapshot: BackupSourceSnapshot | None = None
            try:
                with transaction.atomic():
                    from apps.source.services.internal.source_operation_fence import (
                        assert_source_product_operation_allowed,
                    )

                    assert_source_product_operation_allowed(
                        organization_id=organization_id,
                        source_type=source.source_type,
                        source_ref_id=source.source_ref_id,
                    )
                    locked_config = BackupConfig.objects.select_for_update().get(
                        organization_id=organization_id,
                        id=config.id,
                    )
                    existing_snapshot = _existing_snapshot_by_idempotency(
                        organization_id=organization_id,
                        idempotency_key=item_idempotency_key,
                    )
                    if existing_snapshot is not None:
                        existing_task = Task.objects.filter(
                            organization_id=organization_id,
                            id=existing_snapshot.task_id,
                        ).first()
                        existing_result = {
                            "task_id": existing_task.id if existing_task is not None else existing_snapshot.task_id,
                            "task_uuid": str(existing_task.task_uuid) if existing_task is not None else str(existing_snapshot.task_uuid),
                            "source_snapshot_id": existing_snapshot.id,
                            "source_snapshot_status": existing_snapshot.status,
                            "status": "skipped",
                            "message": "A backup task already exists for this idempotency key.",
                        }
                    else:
                        active_task = find_active_backup_task(
                            organization_id=organization_id,
                            source_type=source.source_type,
                            source_ref_id=source.source_ref_id,
                            backup_config_id=locked_config.id,
                        )
                        if active_task is not None:
                            existing_result = {
                                "task_id": active_task.id,
                                "task_uuid": str(active_task.task_uuid),
                                "source_snapshot_id": None,
                                "source_snapshot_status": None,
                                "status": "conflict",
                                "message": "A backup task for this source and backup config is already running.",
                            }

                    if existing_result is None:
                        directory_count = locked_config.directories.count()
                        task = create_task(
                            organization_id=organization_id,
                            task_type=Task.Type.BACKUP,
                            display_name=task_display_name,
                            trigger_type=task_trigger_type,
                            request_payload={
                                "source_type": source.source_type,
                                "source_ref_id": source.source_ref_id,
                                "backup_config_id": locked_config.id,
                                "repository_id": locked_config.repository_id,
                                "directory_count": directory_count,
                                "directory_size_refresh_required": True,
                            },
                            resources=[
                                {
                                    "resource_type": TaskResource.Type.BACKUP_SOURCE,
                                    "resource_subtype": source.source_type,
                                    "resource_id": source.source_ref_id,
                                    "is_primary": True,
                                },
                                {
                                    "resource_type": TaskResource.Type.REPOSITORY,
                                    "resource_id": locked_config.repository_id,
                                },
                            ],
                            steps=[
                                {"step_name": "create_logic_snapshot"},
                                {"step_name": "kopia_snapshot"},
                                {"step_name": "finalize_snapshot"},
                            ],
                        )
                        snapshot = create_source_snapshot(
                            organization_id=organization_id,
                            source_type=source.source_type,
                            source_ref_id=source.source_ref_id,
                            backup_config_id=locked_config.id,
                            repository_id=locked_config.repository_id,
                            task_id=task.id,
                            task_uuid=task.task_uuid,
                            trigger_type=snapshot_trigger_type,
                            idempotency_key=item_idempotency_key,
                            directory_count=directory_count,
                            metadata={"task_display_name": task.display_name},
                        )

                        from apps.protection.tasks.directory_size_estimate import (
                            refresh_backup_config_directory_estimates_task,
                        )

                        transaction.on_commit(
                            lambda config_id=locked_config.id, task_uuid=str(task.task_uuid): refresh_backup_config_directory_estimates_task.delay(
                                config_id=config_id,
                                force_refresh=True,
                                task_uuid=task_uuid,
                            )
                        )
                        transaction.on_commit(
                            lambda org_id=organization_id, task_uuid=str(task.task_uuid), snapshot_id=snapshot.id: _queue_backup_execution(
                                organization_id=org_id,
                                task_uuid=task_uuid,
                                source_snapshot_id=snapshot_id,
                            )
                        )
            except IntegrityError:
                existing_snapshot = _existing_snapshot_by_idempotency(
                    organization_id=organization_id,
                    idempotency_key=item_idempotency_key,
                )
                if existing_snapshot is not None:
                    existing_task = Task.objects.filter(
                        organization_id=organization_id,
                        id=existing_snapshot.task_id,
                    ).first()
                    existing_result = {
                        "task_id": existing_task.id if existing_task is not None else existing_snapshot.task_id,
                        "task_uuid": str(existing_task.task_uuid) if existing_task is not None else str(existing_snapshot.task_uuid),
                        "source_snapshot_id": existing_snapshot.id,
                        "source_snapshot_status": existing_snapshot.status,
                        "status": "skipped",
                        "message": "A backup task already exists for this idempotency key.",
                    }
                else:
                    active_snapshot = BackupSourceSnapshot.objects.filter(
                        organization_id=organization_id,
                        backup_config_id=config.id,
                        status=BackupSourceSnapshot.Status.CREATING,
                    ).first()
                    active_task = (
                        Task.objects.filter(
                            organization_id=organization_id,
                            id=active_snapshot.task_id,
                        ).first()
                        if active_snapshot is not None
                        else None
                    )
                    if active_snapshot is None:
                        raise
                    existing_result = {
                        "task_id": active_task.id if active_task is not None else active_snapshot.task_id,
                        "task_uuid": str(active_task.task_uuid) if active_task is not None else str(active_snapshot.task_uuid),
                        "source_snapshot_id": active_snapshot.id,
                        "source_snapshot_status": active_snapshot.status,
                        "status": "conflict",
                        "message": "A backup task for this source and backup config is already running.",
                    }

            if existing_result is not None:
                skipped_count += 1
                results.append(
                    {
                        "source_type": source.source_type,
                        "source_ref_id": source.source_ref_id,
                        "backup_config_id": config.id,
                        **existing_result,
                    }
                )
                continue

            if task is None or snapshot is None:
                raise RuntimeError("Backup task creation did not produce a task and snapshot.")

            created_count += 1
            results.append(
                {
                    "source_type": source.source_type,
                    "source_ref_id": source.source_ref_id,
                    "backup_config_id": config.id,
                    "task_id": task.id,
                    "task_uuid": str(task.task_uuid),
                    "source_snapshot_id": snapshot.id,
                    "source_snapshot_status": snapshot.status,
                    "status": "created",
                    "message": "Backup task created.",
                }
            )

    return {
        "created_count": created_count,
        "skipped_count": skipped_count,
        "results": results,
    }


def reconcile_interrupted_backup_tasks(*, limit: int = 100) -> dict[str, int]:
    from apps.protection.services.backup_orchestrator import reconcile_backup_tasks

    return reconcile_backup_tasks(limit=int(limit))


def _run_backup_task_locked(
    *,
    organization_id: int,
    task_uuid: str,
    source_snapshot_id: int,
) -> dict[str, Any]:
    from apps.protection.services.backup_orchestrator import advance_backup

    logger.info(
        "backup task run started task_uuid=%s source_snapshot_id=%s org_id=%s",
        task_uuid,
        source_snapshot_id,
        organization_id,
    )
    return advance_backup(
        organization_id=organization_id,
        task_uuid=task_uuid,
        source_snapshot_id=source_snapshot_id,
    )


def _resolve_execution_target(*, source_snapshot: BackupSourceSnapshot) -> ExecutionTarget:
    return resolve_source_execution_target(
        organization_id=source_snapshot.organization_id,
        source_type=source_snapshot.source_type,
        source_ref_id=source_snapshot.source_ref_id,
    )


def _repository_runtime_payload(
    *,
    repository: Repository,
    execution_target: ExecutionTarget,
    repository_endpoint_type: object = "external",
    repository_endpoint: object = None,
) -> dict[str, Any]:
    return build_repository_runtime_payload(
        repository=repository,
        execution_target=execution_target,
        repository_endpoint_type=repository_endpoint_type,
        repository_endpoint=repository_endpoint,
    )


def _agent_backup_payload(
    *,
    source_path: str,
    backup_config_dir_id: int,
    repository_payload: dict[str, Any],
    nas_payload: dict[str, Any] | None = None,
    file_filter_payload: dict[str, Any] | None = None,
    backup_policy_payload: dict[str, Any] | None = None,
    compression_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "source_path": source_path,
        "backup_config_dir_id": backup_config_dir_id,
        "repository": repository_payload,
    }
    if nas_payload:
        payload["nas"] = nas_payload
    if file_filter_payload:
        payload["file_filter"] = file_filter_payload
    if backup_policy_payload:
        payload["backup_policy"] = backup_policy_payload
    if compression_payload:
        payload["compression"] = compression_payload
    return payload


def _int_result(data: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            continue
        try:
            if value not in (None, ""):
                return int(value)
        except (TypeError, ValueError):
            continue
    return 0


_SNAPSHOT_SIZE_KEYS = (
    "size",
    "size_bytes",
    "sizeBytes",
    "total_size",
    "totalSize",
    "total_size_bytes",
    "totalSizeBytes",
    "total_file_size",
    "totalFileSize",
)
_SNAPSHOT_FILE_COUNT_KEYS = (
    "files",
    "file_count",
    "fileCount",
    "total_file_count",
    "totalFileCount",
    "total_files",
    "totalFiles",
    "num_files",
    "numFiles",
)
_SNAPSHOT_DIR_COUNT_KEYS = (
    "dirs",
    "dir_count",
    "dirCount",
    "directory_count",
    "directoryCount",
    "total_dir_count",
    "totalDirCount",
    "total_directory_count",
    "totalDirectoryCount",
    "total_directories",
    "totalDirectories",
    "num_directories",
    "numDirectories",
)
_SNAPSHOT_STATS_KEYS = ("stats", "summary", "summ", "snapshot", "rootEntry", "root_entry", "root")


def _nested_dict_result(data: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _int_result_deep(data: dict[str, Any], *keys: str) -> int:
    value = _int_result(data, *keys)
    if value > 0:
        return value
    for nested_key in _SNAPSHOT_STATS_KEYS:
        nested = _nested_dict_result(data, nested_key)
        if nested:
            value = _int_result_deep(nested, *keys)
            if value > 0:
                return value
    return 0


def _extract_snapshot_metrics(result: dict[str, Any]) -> tuple[str, int, int, int, dict[str, Any]]:
    snapshot_id = str(
        result.get("kopia_snapshot_id")
        or result.get("snapshot_id")
        or result.get("id")
        or ""
    ).strip()
    stats = _nested_dict_result(result, *_SNAPSHOT_STATS_KEYS)
    size_bytes = _int_result_deep(result, *_SNAPSHOT_SIZE_KEYS)
    file_count = _int_result_deep(result, *_SNAPSHOT_FILE_COUNT_KEYS)
    dir_count = _int_result_deep(result, *_SNAPSHOT_DIR_COUNT_KEYS)
    if stats:
        stats = {
            **stats,
            "size_bytes": size_bytes,
            "file_count": file_count,
            "dir_count": dir_count,
        }
    return snapshot_id, size_bytes, file_count, dir_count, stats


def extract_kopia_failure_message(result: dict[str, Any] | None, *, last_error: str = "") -> str:
    """Pull human-readable Kopia/agent failure text from a NodeTask result payload."""
    if not isinstance(result, dict):
        return str(last_error or "").strip()

    chunks: list[str] = []
    for key in ("stderr", "stderr_tail", "stdout", "stdout_tail"):
        text = str(result.get(key) or "").strip()
        if text:
            chunks.append(text)
    for nested_key in ("snapshot_create", "repository_create", "repository_connect", "repository_status"):
        nested = result.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in ("stderr", "stderr_tail", "stdout", "stdout_tail"):
            text = str(nested.get(key) or "").strip()
            if text:
                chunks.append(text)

    combined = "\n".join(chunks)
    interesting: list[str] = []
    for line in combined.splitlines():
        stripped = line.strip().lstrip("!").strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if (
            "error when processing" in lower
            or "fatal error" in lower
            or "permissiondenied" in lower
            or "permission denied" in lower
            or "access denied" in lower
            or "rpc error:" in lower
            or "failed to open repository" in lower
            or "error connecting to repository" in lower
            or lower.startswith("error:")
            or "unable to get policy tree" in lower
            or "policy not found" in lower
            or "unable to open" in lower
            or "found " in lower and "fatal error" in lower
        ):
            interesting.append(stripped)

    if interesting:
        seen: set[str] = set()
        unique: list[str] = []
        for line in interesting:
            if line in seen:
                continue
            seen.add(line)
            unique.append(line)
        summary = "; ".join(unique[:3])
        if len(unique) > 3:
            summary += f" (+{len(unique) - 3} more)"
        return summary[:2000]

    for line in reversed(combined.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if any(token in lower for token in ("hashing", "hashed", "uploaded", "estimating", "snapshotting")):
            continue
        if stripped:
            return stripped[:2000]

    cleaned_error = str(last_error or "").strip()
    if cleaned_error and not _is_generic_exit_message(cleaned_error):
        return cleaned_error[:2000]
    if combined:
        tail = combined.strip().splitlines()[-1].strip()
        if tail:
            return tail[:2000]
    return cleaned_error[:2000]


def public_repository_failure_message(message: str) -> str:
    """Return an actionable repository error without backend implementation details."""
    text = re.sub(r"https?://\S+", "", str(message or "")).strip()
    lower = text.lower()
    if "not initialized" in lower:
        return "Backup repository is not initialized. Initialize or repair the repository before retrying."
    if "repository is not connected" in lower or "not connected" in lower:
        return "Backup repository is not connected. Check the repository and retry."
    text = re.sub(r"\bKopia\b", "backup repository", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip(" ;")[:2000]


def _is_generic_exit_message(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return True
    if re.fullmatch(r"exit \d+: exit status \d+", text):
        return True
    if re.fullmatch(r"exit status \d+", text):
        return True
    return False


def _directory_error(outcome, *, timed_out: bool = False) -> tuple[str, str]:
    if timed_out:
        return "AGENT_TIMEOUT", "Agent task timed out while creating the Kopia snapshot."
    result = outcome.result if isinstance(outcome.result, dict) else {}
    last_error = str(outcome.task.last_error or "").strip()
    message = extract_kopia_failure_message(result, last_error=last_error)
    if not message:
        message = "Agent backup command failed."
    lower = message.lower()
    if str(result.get("error_code") or "") == "KOPIA_POLICY_NOT_FOUND" or (
        "unable to get policy tree" in lower or "policy not found" in lower
    ):
        return "KOPIA_POLICY_NOT_FOUND", message
    if "fatal error" in lower or "error when processing" in lower:
        return "KOPIA_SNAPSHOT_FATAL", message
    if _is_generic_exit_message(last_error) or "exit" in last_error.lower():
        return "KOPIA_PROCESS_DIED", message
    return "AGENT_BACKUP_FAILED", message


def _directory_snapshot_result(
    *,
    source_snapshot: BackupSourceSnapshot,
    backup_config_dir_id: int,
) -> BackupSourceSnapshotDirectory | None:
    return BackupSourceSnapshotDirectory.objects.filter(
        source_snapshot=source_snapshot,
        backup_config_dir_id=int(backup_config_dir_id),
    ).first()


def _matching_backup_node_task(
    *,
    organization_id: int,
    node_id: int,
    task_uuid: str,
    backup_config_dir_id: int,
) -> NodeTask | None:
    candidates = NodeTask.objects.filter(
        organization_id=organization_id,
        node_id=node_id,
        kind__in=protection_conf.PROTECTION_BACKUP_NODE_TASK_KINDS,
        correlation_type="protection.backup",
        correlation_id=str(task_uuid),
    ).order_by("-created_at", "-id")
    for candidate in candidates:
        payload = candidate.payload if isinstance(candidate.payload, dict) else {}
        try:
            payload_dir_id = int(payload.get("backup_config_dir_id") or 0)
        except (TypeError, ValueError):
            continue
        if payload_dir_id == int(backup_config_dir_id):
            return candidate
    return None


def _agent_task_result(
    *,
    task: Task,
    organization_id: int,
    node_id: int,
    source_path: str,
    directory_index: int,
    total_dirs: int,
    backup_config_dir_id: int,
    repository_payload: dict[str, Any],
    nas_payload: dict[str, Any] | None,
    file_filter_payload: dict[str, Any] | None,
    backup_policy_payload: dict[str, Any] | None,
    wait_timeout_seconds: int,
) -> AgentTaskSyncResult:
    last_applied_percent: dict[str, float] = {"value": -1.0}

    def _on_agent_stream(stream_message: dict[str, Any]) -> None:
        progress = stream_message.get("progress")
        if not isinstance(progress, dict) or not progress:
            return
        _apply_backup_agent_progress(
            task=task,
            directory_index=directory_index,
            total_dirs=total_dirs,
            progress=progress,
            last_applied_percent=last_applied_percent,
        )

    existing_node_task = _matching_backup_node_task(
        organization_id=organization_id,
        node_id=node_id,
        task_uuid=str(task.task_uuid),
        backup_config_dir_id=backup_config_dir_id,
    )
    if existing_node_task is not None:
        if existing_node_task.status == NodeTask.Status.PENDING:
            existing_node_task = deliver_agent_task(task=existing_node_task)
        if existing_node_task.status in _NODE_TASK_TERMINAL_STATUSES:
            return AgentTaskSyncResult(
                task=existing_node_task,
                stream_message=None,
                timed_out=existing_node_task.status == NodeTask.Status.TIMEOUT,
            )
        append_task_step_event(
            task=task,
            step_name="kopia_snapshot",
            message="Resuming directory backup agent task",
            metadata={
                "backup_config_dir_id": backup_config_dir_id,
                "source_path": source_path,
                "node_task_id": str(existing_node_task.id),
                "node_task_status": existing_node_task.status,
                "wait_timeout_seconds": wait_timeout_seconds,
                "object_name": source_path,
            },
        )
        return wait_for_agent_task(
            task_id=existing_node_task.id,
            timeout_seconds=wait_timeout_seconds,
            on_stream_message=_on_agent_stream,
        )

    return run_agent_task_sync(
        organization_id=organization_id,
        node_id=node_id,
        kind="backup.run",
        payload=_agent_backup_payload(
            source_path=source_path,
            backup_config_dir_id=backup_config_dir_id,
            repository_payload=repository_payload,
            nas_payload=nas_payload,
            file_filter_payload=file_filter_payload,
            backup_policy_payload=backup_policy_payload,
        ),
        correlation_type="protection.backup",
        correlation_id=str(task.task_uuid),
        wait_timeout_seconds=wait_timeout_seconds,
        on_stream_message=_on_agent_stream,
    )


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        for messages in exc.message_dict.values():
            if messages:
                return str(messages[0])
    messages = getattr(exc, "messages", None)
    if messages:
        return str(messages[0])
    return str(exc)


def run_backup_task(
    *,
    organization_id: int,
    task_uuid: str,
    source_snapshot_id: int,
) -> dict[str, Any]:
    task_uuid_value = str(task_uuid)
    if not _acquire_backup_execution_lock(
        organization_id=organization_id,
        task_uuid=task_uuid_value,
    ):
        return {
            "task_uuid": task_uuid_value,
            "source_snapshot_id": source_snapshot_id,
            "status": "already_running",
        }
    try:
        return _run_backup_task_locked(
            organization_id=organization_id,
            task_uuid=task_uuid_value,
            source_snapshot_id=source_snapshot_id,
        )
    finally:
        _release_backup_execution_lock(
            organization_id=organization_id,
            task_uuid=task_uuid_value,
        )
