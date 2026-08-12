"""Directory size (du) estimates for backup progress — never a sync start gate."""

from __future__ import annotations

import logging
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.node.services.interface import run_agent_task_sync
from apps.protection.models import BackupConfig, BackupConfigDirectory, BackupSourceSnapshot
from apps.protection.services.backup_task import ExecutionTarget, _resolve_execution_target
from apps.source.services.internal.nas_share_path import to_mount_path

logger = logging.getLogger(__name__)

_PATH_SIZE_KIND = "path.size"
# Legacy sync callers (if any) may still use a long wait; async precache uses a
# shorter per-path budget so multiple directories fit under the Celery soft limit.
_PATH_SIZE_TIMEOUT_SECONDS = 300
_PRECACHE_PATH_TIMEOUT_SECONDS = 60
_PRECACHE_MAX_ATTEMPTS = 5
# Stored when path.size was attempted but failed. Progress treats as 0;
# gating skips re-queue until the cache is invalidated back to 0.
_ESTIMATE_UNAVAILABLE = -1


class DirectorySizeEstimateError(ValidationError):
    """Raised when directory size cannot be estimated.

    permanent=True means the path should not be retried until cache invalidation.
    Timeouts and transient agent errors stay retryable.
    """

    def __init__(self, message, *, permanent: bool = False, code=None, params=None):
        super().__init__(message, code=code, params=params)
        self.permanent = bool(permanent)


class DirectorySizeEstimateResolveError(Exception):
    """Raised when the backup execution target cannot be resolved for du precache."""


def _agent_path_for_directory(
    *,
    directory: BackupConfigDirectory,
    execution_target: ExecutionTarget,
) -> str:
    path = str(directory.path or "").strip()
    if execution_target.source_type != "agent" and execution_target.root_path:
        return to_mount_path(execution_target.root_path, path)
    return path


def estimate_directory_size_bytes(
    *,
    node_id: int,
    path: str,
    path_type: str = "directory",
    organization_id: int,
    execution_target: ExecutionTarget,
    wait_timeout_seconds: int | None = None,
) -> int:
    timeout = (
        _PATH_SIZE_TIMEOUT_SECONDS
        if wait_timeout_seconds is None
        else max(1, int(wait_timeout_seconds))
    )
    payload: dict[str, Any] = {
        "path": path,
        "path_type": path_type,
    }
    if execution_target.nas_payload:
        payload["nas"] = execution_target.nas_payload
    outcome = run_agent_task_sync(
        organization_id=organization_id,
        node_id=node_id,
        kind=_PATH_SIZE_KIND,
        payload=payload,
        wait_timeout_seconds=timeout,
    )
    if outcome.timed_out:
        raise DirectorySizeEstimateError(
            f"Path size estimation timed out for {path}",
            permanent=False,
        )
    if not outcome.ok:
        error = str(outcome.task.last_error or "").strip()
        if not error and isinstance(outcome.stream_message, dict):
            error = str(
                outcome.stream_message.get("error")
                or outcome.stream_message.get("message")
                or ""
            )
        if not error:
            error = "path size estimation failed"
        # Agent/host blips are retryable; leave estimate pending for requeue.
        raise DirectorySizeEstimateError(error, permanent=False)
    result = outcome.result
    if not isinstance(result, dict) or "size_bytes" not in result:
        raise DirectorySizeEstimateError("Agent returned an invalid path size response.")
    raw_size_bytes = result["size_bytes"]
    if isinstance(raw_size_bytes, bool):
        raise DirectorySizeEstimateError("Agent returned an invalid path size response.")
    try:
        size_bytes = int(raw_size_bytes)
    except (TypeError, ValueError):
        raise DirectorySizeEstimateError("Agent returned an invalid path size response.") from None
    if size_bytes < 0:
        raise DirectorySizeEstimateError("Agent returned an invalid path size response.")
    return size_bytes


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
        ).update(estimated_size_bytes=0, size_estimated_at=None)
    )


def _mark_pending_directory_estimates_unavailable(*, config: BackupConfig) -> int:
    """Stop retrying pending paths until an explicit cache invalidation."""
    return int(
        config.directories.filter(
            estimated_size_bytes=0,
            size_estimated_at__isnull=True,
        ).update(
            estimated_size_bytes=_ESTIMATE_UNAVAILABLE
        )
    )


def refresh_missing_backup_config_directory_estimates(
    *,
    organization_id: int,
    config: BackupConfig,
    source_type: str,
    source_ref_id: int,
    wait_timeout_seconds: int | None = None,
) -> int:
    """Best-effort refresh of missing directory estimates.

    Failures are logged and skipped. Returns the sum of known estimates (may be 0).
    Progress UI degrades when the total is 0; backup must not fail because of this.

    Raises DirectorySizeEstimateResolveError when the execution target cannot be
    resolved (caller may requeue within the attempt budget).
    """
    directories = list(config.directories.all())
    if not any(_directory_estimate_pending(directory) for directory in directories):
        return _du_total_from_directories(directories)

    snapshot_stub = BackupSourceSnapshot(
        organization_id=organization_id,
        source_type=source_type,
        source_ref_id=source_ref_id,
        backup_config_id=config.id,
    )
    try:
        execution_target = _resolve_execution_target(source_snapshot=snapshot_stub)
    except Exception as exc:
        logger.exception(
            "directory_size_estimate_resolve_failed config_id=%s source=%s:%s",
            config.id,
            source_type,
            source_ref_id,
        )
        raise DirectorySizeEstimateResolveError(str(exc) or "resolve failed") from exc

    node_id = int(execution_target.node.id)
    du_total = 0
    for directory in directories:
        current = max(0, _raw_estimate(directory))
        if _directory_estimate_pending(directory):
            agent_path = _agent_path_for_directory(
                directory=directory,
                execution_target=execution_target,
            )
            path_type = (
                str(directory.path_type or "directory").strip().lower() or "directory"
            )
            try:
                estimated = estimate_directory_size_bytes(
                    node_id=node_id,
                    path=agent_path,
                    path_type=path_type,
                    organization_id=organization_id,
                    execution_target=execution_target,
                    wait_timeout_seconds=wait_timeout_seconds,
                )
            except SoftTimeLimitExceeded:
                raise
            except DirectorySizeEstimateError as exc:
                logger.warning(
                    "directory_size_estimate_failed config_id=%s directory_id=%s "
                    "path=%s permanent=%s error=%s",
                    config.id,
                    directory.id,
                    agent_path,
                    exc.permanent,
                    exc,
                )
                if exc.permanent:
                    directory.estimated_size_bytes = _ESTIMATE_UNAVAILABLE
                    directory.save(update_fields=["estimated_size_bytes", "updated_at"])
                continue
            except Exception as exc:
                # Unexpected errors stay retryable (estimate remains 0).
                logger.warning(
                    "directory_size_estimate_failed config_id=%s directory_id=%s "
                    "path=%s error=%s",
                    config.id,
                    directory.id,
                    agent_path,
                    exc,
                )
                continue
            directory.estimated_size_bytes = estimated
            directory.size_estimated_at = timezone.now()
            directory.save(
                update_fields=["estimated_size_bytes", "size_estimated_at", "updated_at"]
            )
            current = estimated
            logger.info(
                "directory_size_estimated config_id=%s directory_id=%s path=%s "
                "size_bytes=%s",
                config.id,
                directory.id,
                agent_path,
                estimated,
            )
        du_total += current
    return du_total


def refresh_backup_config_directory_estimates_by_id(
    *,
    config_id: int,
    attempt: int = 1,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Celery entry: pre-cache missing estimates for one backup config."""
    attempt_n = max(1, int(attempt or 1))
    config = BackupConfig.objects.filter(id=int(config_id)).first()
    if config is None:
        return {
            "config_id": int(config_id),
            "status": "missing",
            "du_total": 0,
            "attempt": attempt_n,
            "should_requeue": False,
        }

    # A newly created backup must not reuse a size cached when the configured
    # path contained different files. Invalidate once, then let the existing
    # bounded retry flow handle every directory as a normal pending estimate.
    if force_refresh and attempt_n == 1:
        invalidate_backup_config_directory_estimates(config=config)

    timed_out = False
    resolve_failed = False
    try:
        du_total = refresh_missing_backup_config_directory_estimates(
            organization_id=int(config.organization_id),
            config=config,
            source_type=str(config.source_type),
            source_ref_id=int(config.source_ref_id),
            wait_timeout_seconds=_PRECACHE_PATH_TIMEOUT_SECONDS,
        )
    except SoftTimeLimitExceeded:
        timed_out = True
        logger.warning(
            "directory_size_estimate_soft_time_limit config_id=%s attempt=%s",
            config.id,
            attempt_n,
        )
        du_total = _du_total(config)
    except DirectorySizeEstimateResolveError:
        resolve_failed = True
        du_total = _du_total(config)

    still_pending = backup_config_needs_directory_estimate_refresh(config)
    should_requeue = bool(still_pending and attempt_n < _PRECACHE_MAX_ATTEMPTS)
    # Only freeze when the source/target cannot be resolved: retryable path.size
    # timeouts stay at 0 so a later directories/source save can precache again.
    # Name-only updates do not enqueue (see backup_config views).
    if resolve_failed and still_pending and not should_requeue:
        _mark_pending_directory_estimates_unavailable(config=config)

    if timed_out and should_requeue:
        status = "soft_time_limit"
    elif timed_out and still_pending:
        status = "soft_time_limit_exhausted"
    elif resolve_failed and should_requeue:
        status = "resolve_failed"
    elif resolve_failed:
        status = "resolve_exhausted"
    elif should_requeue:
        status = "partial"
    elif still_pending:
        status = "exhausted"
    else:
        status = "ok"
    return {
        "config_id": int(config.id),
        "status": status,
        "du_total": int(du_total),
        "du_total_known": _all_directory_estimates_verified(config),
        "attempt": attempt_n,
        "should_requeue": should_requeue,
    }
