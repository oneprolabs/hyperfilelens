"""Directory size (du) estimates for backup progress — never a sync start gate."""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.services.interface import cancel_agent_task, run_agent_task_async
from apps.protection.models import BackupConfig, BackupConfigDirectory, BackupSourceSnapshot
from apps.protection.services.backup_task import ExecutionTarget, _resolve_execution_target
from apps.source.services.internal.nas_share_path import to_mount_path

logger = logging.getLogger(__name__)

_PATH_SIZE_KIND = "path.size"
_PATH_SIZE_CORRELATION_TYPE = node_conf.PATH_SIZE_CORRELATION_TYPE
_PATH_SIZE_MONITOR_SECONDS = 30
_PATH_SIZE_RETRY_SECONDS = 30
# Stored when path.size was attempted but failed. Progress treats as 0;
# gating skips re-queue until the cache is invalidated back to 0.
_ESTIMATE_UNAVAILABLE = -1


def _path_size_concurrency(execution_target: ExecutionTarget) -> int:
    role = str(execution_target.node.role or "").strip().lower()
    return 2 if role in {"proxy", "gateway"} else 1


def _schedule_directory_estimate_refresh(
    *,
    config_id: int,
    task_uuid: str | None,
    countdown: int = 0,
) -> None:
    from apps.protection.tasks.directory_size_estimate import (
        refresh_backup_config_directory_estimates_task,
    )

    refresh_backup_config_directory_estimates_task.apply_async(
        kwargs={"config_id": int(config_id), "task_uuid": task_uuid},
        countdown=max(0, int(countdown)),
    )


def _agent_path_for_directory(
    *,
    directory: BackupConfigDirectory,
    execution_target: ExecutionTarget,
) -> str:
    path = str(directory.path or "").strip()
    if execution_target.source_type != "agent" and execution_target.root_path:
        return to_mount_path(execution_target.root_path, path)
    return path


def directory_size_correlation_id(
    *,
    config: BackupConfig,
    directory: BackupConfigDirectory,
) -> str:
    """Return the stable identity for one configuration generation."""
    return f"{config.id}:{directory.id}:{directory.updated_at.isoformat()}"


def _mark_estimate_unavailable_if_current(
    *,
    config: BackupConfig,
    directory_id: int,
    correlation_id: str,
) -> bool:
    with transaction.atomic():
        directory = (
            BackupConfigDirectory.objects.select_for_update()
            .filter(id=directory_id, backup_config_id=config.id)
            .first()
        )
        if directory is None or directory_size_correlation_id(
            config=config,
            directory=directory,
        ) != correlation_id:
            return False
        directory.estimated_size_bytes = _ESTIMATE_UNAVAILABLE
        directory.save(update_fields=["estimated_size_bytes", "updated_at"])
        return True


def _path_size_failure_is_retryable(node_task: NodeTask) -> bool:
    result = node_task.result if isinstance(node_task.result, dict) else {}
    code = str(
        result.get("diagnostic_error_code") or result.get("error_code") or ""
    ).strip().upper()
    error = str(node_task.last_error or "").strip().lower()
    return code in {
        "AGENT_UNAVAILABLE",
        "AGENT_CONNECTION_UNSTABLE",
        "AGENT_ACK_TIMEOUT",
        "AGENT_WEBSOCKET_UNAVAILABLE",
    } or any(
        marker in error
        for marker in (
            "websocket is not routable",
            "websocket is reconnecting",
            "agent is offline",
            "agent source is offline",
        )
    )


def _path_size_retry_exhausted(*, correlation_id: str) -> bool:
    retry_count = NodeTask.objects.filter(
        kind=_PATH_SIZE_KIND,
        correlation_type=_PATH_SIZE_CORRELATION_TYPE,
        correlation_id=correlation_id,
        status=NodeTask.Status.FAILED,
    ).count()
    return retry_count >= node_conf.PATH_SIZE_MAX_RETRIES


def enqueue_backup_config_directory_estimates(
    *,
    config_id: int,
    task_uuid: str | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Dispatch missing directory estimates without waiting for the Agent."""
    config = BackupConfig.objects.filter(id=int(config_id)).first()
    if config is None:
        return {"config_id": int(config_id), "status": "missing", "queued": 0}
    if force_refresh:
        # Keep compatibility for explicit refresh callers, but do not make
        # ordinary backup execution invalidate a usable cached estimate.
        invalidate_backup_config_directory_estimates(config=config)
        config.refresh_from_db()
    directories = list(config.directories.all())
    if not any(_directory_estimate_pending(item) for item in directories):
        return {"config_id": int(config.id), "status": "ok", "queued": 0}

    snapshot_stub = BackupSourceSnapshot(
        organization_id=config.organization_id,
        source_type=config.source_type,
        source_ref_id=config.source_ref_id,
        backup_config_id=config.id,
    )
    try:
        execution_target = _resolve_execution_target(source_snapshot=snapshot_stub)
    except Exception as exc:
        logger.warning(
            "directory_size_estimate_resolve_failed config_id=%s error=%s",
            config.id,
            exc,
        )
        return {
            "config_id": int(config.id),
            "status": "resolve_failed",
            "queued": 0,
        }

    queued = 0
    dispatch_attempts = 0
    dispatch_capacity = _path_size_concurrency(execution_target)
    dispatch_failed = False
    for directory in directories:
        if not _directory_estimate_pending(directory):
            continue
        node_task_id = ""
        correlation_id = ""
        with transaction.atomic():
            locked_node = (
                Node.objects.select_for_update()
                .filter(pk=execution_target.node.id)
                .first()
            )
            if locked_node is None:
                continue
            locked = (
                BackupConfigDirectory.objects.select_for_update()
                .filter(id=directory.id, backup_config_id=config.id)
                .first()
            )
            if locked is None or not _directory_estimate_pending(locked):
                continue
            correlation_id = directory_size_correlation_id(
                config=config,
                directory=locked,
            )
            active = (
                NodeTask.objects.filter(
                    organization_id=config.organization_id,
                    node_id=execution_target.node.id,
                    kind=_PATH_SIZE_KIND,
                    correlation_type=_PATH_SIZE_CORRELATION_TYPE,
                    correlation_id=correlation_id,
                    status__in=(NodeTask.Status.PENDING, NodeTask.Status.RUNNING),
                )
                .order_by("created_at", "id")
                .first()
            )
            if active is not None:
                node_task_id = str(active.id)
            else:
                active_count = NodeTask.objects.filter(
                    organization_id=config.organization_id,
                    node_id=locked_node.id,
                    kind=_PATH_SIZE_KIND,
                    correlation_type=_PATH_SIZE_CORRELATION_TYPE,
                    status__in=(
                        NodeTask.Status.PENDING,
                        NodeTask.Status.RUNNING,
                    ),
                ).count()
                if (
                    dispatch_attempts >= dispatch_capacity
                    or active_count >= dispatch_capacity
                ):
                    continue
                agent_path = _agent_path_for_directory(
                    directory=locked,
                    execution_target=execution_target,
                )
                path_type = (
                    str(locked.path_type or "directory").strip().lower() or "directory"
                )
                payload: dict[str, Any] = {
                    "path": agent_path,
                    "path_type": path_type,
                }
                if execution_target.nas_payload:
                    payload["nas"] = execution_target.nas_payload
                try:
                    dispatch_attempts += 1
                    handle = run_agent_task_async(
                        organization_id=int(config.organization_id),
                        node_id=int(execution_target.node.id),
                        kind=_PATH_SIZE_KIND,
                        payload=payload,
                        correlation_type=_PATH_SIZE_CORRELATION_TYPE,
                        correlation_id=correlation_id,
                    )
                except Exception:
                    dispatch_failed = True
                    logger.exception(
                        "directory_size_estimate_dispatch_failed config_id=%s "
                        "directory_id=%s",
                        config.id,
                        locked.id,
                    )
                    continue
                node_task_id = str(handle.task_id)
                queued += 1
            directory_id = locked.id

        if not node_task_id:
            continue
        from apps.protection.tasks.directory_size_estimate import (
            reconcile_directory_size_estimate_task,
        )

        reconcile_directory_size_estimate_task.apply_async(
            kwargs={
                "config_id": int(config.id),
                "directory_id": int(directory_id),
                "node_task_id": node_task_id,
                "correlation_id": correlation_id,
                "task_uuid": task_uuid,
            },
            countdown=_PATH_SIZE_MONITOR_SECONDS,
        )
    status = "pending"
    if queued:
        status = "queued"
    elif dispatch_failed:
        status = "dispatch_failed"
    return {"config_id": int(config.id), "status": status, "queued": queued}


def reconcile_directory_size_estimate(
    *,
    config_id: int,
    directory_id: int,
    node_task_id: str,
    correlation_id: str,
    task_uuid: str | None = None,
) -> dict[str, Any]:
    """Apply one asynchronous estimate or schedule its next observation."""
    node_task = NodeTask.objects.filter(id=node_task_id).first()
    config = BackupConfig.objects.filter(id=int(config_id)).first()
    directory = BackupConfigDirectory.objects.filter(
        id=int(directory_id),
        backup_config_id=int(config_id),
    ).first()
    if node_task is None or config is None or directory is None:
        return {"status": "missing"}
    if node_task.correlation_id != correlation_id:
        return {"status": "stale"}
    current_correlation = directory_size_correlation_id(
        config=config,
        directory=directory,
    )
    if current_correlation != correlation_id:
        return {"status": "stale"}
    if node_task.status in (NodeTask.Status.PENDING, NodeTask.Status.RUNNING):
        from apps.protection.tasks.directory_size_estimate import (
            reconcile_directory_size_estimate_task,
        )

        if node_task.watchdog_deadline_at <= timezone.now():
            if node_task.cancel_requested_at is None:
                cancel_agent_task(task_id=str(node_task.id), reason="path size timeout")
            reconcile_directory_size_estimate_task.apply_async(
                kwargs={
                    "config_id": int(config_id),
                    "directory_id": int(directory_id),
                    "node_task_id": str(node_task.id),
                    "correlation_id": correlation_id,
                    "task_uuid": task_uuid,
                },
                countdown=_PATH_SIZE_MONITOR_SECONDS,
            )
            return {"status": "canceling"}
        reconcile_directory_size_estimate_task.apply_async(
            kwargs={
                "config_id": int(config_id),
                "directory_id": int(directory_id),
                "node_task_id": str(node_task.id),
                "correlation_id": correlation_id,
                "task_uuid": task_uuid,
            },
            countdown=_PATH_SIZE_MONITOR_SECONDS,
        )
        return {"status": "pending"}
    if node_task.status == NodeTask.Status.SUCCESS:
        result = node_task.result if isinstance(node_task.result, dict) else {}
        raw_size = result.get("size_bytes")
        if isinstance(raw_size, bool):
            raw_size = None
        try:
            size_bytes = int(raw_size)
        except (TypeError, ValueError):
            size_bytes = -1
        if size_bytes >= 0:
            with transaction.atomic():
                locked_directory = (
                    BackupConfigDirectory.objects.select_for_update()
                    .filter(
                        id=directory.id,
                        backup_config_id=config.id,
                    )
                    .first()
                )
                if locked_directory is None:
                    return {"status": "missing"}
                if (
                    directory_size_correlation_id(
                        config=config,
                        directory=locked_directory,
                    )
                    != correlation_id
                ):
                    return {"status": "stale"}
                locked_directory.estimated_size_bytes = size_bytes
                locked_directory.size_estimated_at = timezone.now()
                locked_directory.save(
                    update_fields=[
                        "estimated_size_bytes",
                        "size_estimated_at",
                        "updated_at",
                    ]
                )
                directory = locked_directory
            if task_uuid and _all_directory_estimates_verified(config):
                from apps.protection.tasks.directory_size_estimate import (
                    _freeze_task_directory_size,
                )

                _freeze_task_directory_size(
                    task_uuid=task_uuid,
                    du_total=_du_total(config),
                )
            _schedule_directory_estimate_refresh(
                config_id=config.id,
                task_uuid=task_uuid,
            )
            return {"status": "success", "size_bytes": size_bytes}
    result = node_task.result if isinstance(node_task.result, dict) else {}
    error_code = str(result.get("error_code") or "").strip().upper()
    if error_code == "PATH_SIZE_BUSY":
        if _path_size_retry_exhausted(correlation_id=correlation_id):
            _mark_estimate_unavailable_if_current(
                config=config,
                directory_id=directory.id,
                correlation_id=correlation_id,
            )
            _schedule_directory_estimate_refresh(
                config_id=config.id,
                task_uuid=task_uuid,
            )
            return {"status": "unavailable"}
        _schedule_directory_estimate_refresh(
            config_id=config_id,
            task_uuid=task_uuid,
            countdown=_PATH_SIZE_RETRY_SECONDS,
        )
        return {"status": "busy"}
    if (
        error_code == "PATH_PERMISSION_DENIED"
        or "path not found" in str(node_task.last_error or "").lower()
    ):
        _mark_estimate_unavailable_if_current(
            config=config,
            directory_id=directory.id,
            correlation_id=correlation_id,
        )
        _schedule_directory_estimate_refresh(
            config_id=config.id,
            task_uuid=task_uuid,
        )
        return {"status": "unavailable"}
    if _path_size_failure_is_retryable(node_task):
        if _path_size_retry_exhausted(correlation_id=correlation_id):
            _mark_estimate_unavailable_if_current(
                config=config,
                directory_id=directory.id,
                correlation_id=correlation_id,
            )
            _schedule_directory_estimate_refresh(
                config_id=config.id,
                task_uuid=task_uuid,
            )
            return {"status": "unavailable"}
        _schedule_directory_estimate_refresh(
            config_id=config.id,
            task_uuid=task_uuid,
            countdown=_PATH_SIZE_RETRY_SECONDS,
        )
        return {"status": "retryable"}
    _mark_estimate_unavailable_if_current(
        config=config,
        directory_id=directory.id,
        correlation_id=correlation_id,
    )
    _schedule_directory_estimate_refresh(
        config_id=config.id,
        task_uuid=task_uuid,
    )
    return {"status": "failed"}


def _raw_estimate(directory: BackupConfigDirectory) -> int:
    return int(directory.estimated_size_bytes or 0)


def _directory_estimate_pending(directory: BackupConfigDirectory) -> bool:
    """True when estimate has never been successfully cached or marked unavailable."""
    return _raw_estimate(directory) == 0 and directory.size_estimated_at is None


def _du_total_from_directories(directories) -> int:
    total = 0
    for directory in directories:
        total += max(0, _raw_estimate(directory))
    return total


def _du_total(config: BackupConfig) -> int:
    return _du_total_from_directories(config.directories.all())


def _all_directory_estimates_verified(config: BackupConfig) -> bool:
    directories = list(config.directories.all())
    return bool(directories) and all(
        directory.size_estimated_at is not None for directory in directories
    )


def backup_config_needs_directory_estimate_refresh(config: BackupConfig) -> bool:
    """True when at least one directory still needs a path.size attempt."""
    return any(
        _directory_estimate_pending(directory)
        for directory in config.directories.all()
    )


def invalidate_backup_config_directory_estimates(*, config: BackupConfig) -> int:
    """Clear cached estimates so async precache will retry (e.g. source rebinding)."""
    return int(
        config.directories.exclude(
            estimated_size_bytes=0,
            size_estimated_at__isnull=True,
        ).update(
            estimated_size_bytes=0,
            size_estimated_at=None,
            updated_at=timezone.now(),
        )
    )


def mark_backup_config_pending_estimates_unavailable(*, config_id: int) -> int:
    """Stop retrying unresolved estimates after the bounded retry budget."""
    return int(
        BackupConfigDirectory.objects.filter(
            backup_config_id=int(config_id),
            estimated_size_bytes=0,
            size_estimated_at__isnull=True,
        ).update(estimated_size_bytes=_ESTIMATE_UNAVAILABLE)
    )
