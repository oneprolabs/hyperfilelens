"""
Async backup orchestration — control plane state machine for long-running Kopia backups.

Celery / reconcile ticks call ``advance_backup``; Agent executes work; no sync blocking.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.node.models import Node, NodeTask
from apps.node.services.internal.node_registry import effective_agent_node_status
from apps.node.services.interface import (
    cancel_agent_task,
    redeliver_pending_agent_task,
    run_agent_task_async,
)
from apps.protection import conf as protection_conf
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.protection.services.backup_source_snapshot import (
    backup_config_directories,
    get_backup_config_for_snapshot,
    mark_source_snapshot_failed,
    record_source_snapshot_directory_result,
    refresh_source_snapshot_summary,
    set_source_snapshot_started,
)
from apps.protection.services.kopia_snapshot_delete import normalize_kopia_snapshot_id
from apps.protection.services.backup_runtime_policy import backup_runtime_policy_payload
from apps.protection.services.progress.orchestrated_progress import (
    BACKUP_LOGIC_END,
    BACKUP_PREPARE_END,
    BACKUP_TRANSFER_END,
)
from apps.protection.services.snapshot_repository_locator import (
    ensure_snapshot_repository_locator,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.repository_access import (
    explicit_repository_server_host,
    resolve_repository_reader,
)
from apps.storage.services.internal.repository_usage import enqueue_repository_usage_refresh
from apps.task.models import Task, TaskEvent, TaskStep
from apps.task.services.interface import append_task_step_event, complete_task, start_task
from apps.task.signals import task_updated

logger = logging.getLogger(__name__)

_BACKUP_PROTOCOL_LEGACY = "legacy_parallel"
_BACKUP_PROTOCOL_PREPARED = "prepared_snapshot_v1"
_BACKUP_PREPARED_CAPABILITY = "backup_prepared_snapshot_v1"
_BACKUP_OPERATION_RECONCILE_CAPABILITY = "backup_operation_reconcile_v1"

_NODE_TASK_TERMINAL = frozenset(
    {
        NodeTask.Status.SUCCESS,
        NodeTask.Status.FAILED,
        NodeTask.Status.TIMEOUT,
        NodeTask.Status.CANCELED,
    }
)
_TASK_TERMINAL = frozenset(
    {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }
)
_DIRECTORY_TERMINAL = frozenset(
    {
        BackupSourceSnapshotDirectory.Status.AVAILABLE,
        BackupSourceSnapshotDirectory.Status.FAILED,
        BackupSourceSnapshotDirectory.Status.CANCELLED,
        BackupSourceSnapshotDirectory.Status.DELETED,
    }
)
_DIRECTORY_IN_PROGRESS = frozenset(
    {
        BackupSourceSnapshotDirectory.Status.PENDING,
        BackupSourceSnapshotDirectory.Status.DISPATCHING,
        BackupSourceSnapshotDirectory.Status.RUNNING,
        BackupSourceSnapshotDirectory.Status.CREATING,
    }
)

_DELIVERY_DIAGNOSTIC_MESSAGES = {
    "AGENT_ACK_TIMEOUT": (
        "The Agent did not acknowledge the backup command. Check Agent "
        "connectivity and version, then retry."
    ),
    "AGENT_CONNECTION_UNSTABLE": (
        "The Agent connection remained unstable. Check the Agent service, "
        "host disk, and network, then retry."
    ),
    "AGENT_UNAVAILABLE": (
        "The Agent is unavailable. Bring the Agent online, then retry."
    ),
    "AGENT_DELIVERY_FAILED": (
        "The backup command could not be delivered. Check platform and Agent "
        "connectivity, then retry."
    ),
}


def _bt():
    from apps.protection.services import backup_task as backup_task_module

    return backup_task_module


def _directory_in_progress(status: str) -> bool:
    return str(status or "").strip().lower() in _DIRECTORY_IN_PROGRESS


def _redeliver_pending_node_task_after_commit(*, node_task: NodeTask) -> None:
    """Resume persisted work only after the orchestration transaction commits."""
    task_id = str(node_task.id)
    transaction.on_commit(
        lambda bound_task_id=task_id: redeliver_pending_agent_task(
            task_id=bound_task_id
        )
    )


def _node_task_error_code(node_task: NodeTask) -> tuple[str, str]:
    bt = _bt()
    last_error = str(node_task.last_error or "").strip()
    lower = last_error.lower()
    result = node_task.result if isinstance(node_task.result, dict) else {}
    diagnostic_error = str(result.get("diagnostic_error_code") or "")
    if (
        node_task.status in {NodeTask.Status.FAILED, NodeTask.Status.TIMEOUT}
        and diagnostic_error in _DELIVERY_DIAGNOSTIC_MESSAGES
    ):
        return diagnostic_error, _DELIVERY_DIAGNOSTIC_MESSAGES[diagnostic_error]
    if node_task.status == NodeTask.Status.TIMEOUT:
        if diagnostic_error == "RESULT_ACK_TIMEOUT":
            return "RESULT_ACK_TIMEOUT", last_error or "Agent result acknowledgement timed out."
        return "WATCHDOG_STALL", last_error or "Agent task watchdog timed out."
    if node_task.status == NodeTask.Status.CANCELED:
        if "progress stall" in lower or "kopia_progress_stall" in lower:
            return "KOPIA_PROGRESS_STALL", last_error
        return "USER_CANCELLED", last_error or "Backup canceled."
    if "agent restarted before task completed" in lower:
        return "AGENT_RESTARTED", last_error
    if "progress stall" in lower or "kopia_progress_stall" in lower:
        return "KOPIA_PROGRESS_STALL", last_error
    if "signal" in lower or "sigkill" in lower or "exit 137" in lower or "exit 9" in lower:
        return "KOPIA_SIGNAL_KILLED", last_error or "Kopia process was killed."
    if node_task.status == NodeTask.Status.FAILED:
        structured_error = str(result.get("error_code") or "")
        if structured_error == "KOPIA_SNAPSHOT_RECONCILE_FAILED":
            return (
                "KOPIA_SNAPSHOT_RECONCILE_FAILED",
                "The existing backup result could not be verified safely. Retry after checking repository availability.",
            )
        if structured_error == "BACKUP_OPERATION_ID_INVALID":
            return (
                "BACKUP_OPERATION_ID_INVALID",
                "The backup execution identity is invalid. Start a new backup task.",
            )
        if structured_error == "KOPIA_POLICY_NOT_FOUND":
            message = bt.extract_kopia_failure_message(result, last_error=last_error)
            return "KOPIA_POLICY_NOT_FOUND", (message or "Kopia policy not found.")[:2000]
        if structured_error == "POLICY_APPLY_FAILED":
            phase = str(result.get("policy_phase") or "apply")
            message = bt.extract_kopia_failure_message(result, last_error=last_error)
            public_message = bt.public_repository_failure_message(message)
            return "POLICY_APPLY_FAILED", (
                public_message or f"Backup repository policy {phase} failed."
            )[:2000]
        message = bt.extract_kopia_failure_message(result, last_error=last_error)
        if not message:
            message = last_error or "Agent backup command failed."
        lower = message.lower()
        if "unable to get policy tree" in lower or "policy not found" in lower:
            return "KOPIA_POLICY_NOT_FOUND", message[:2000]
        if "fatal error" in lower or "error when processing" in lower:
            return "KOPIA_SNAPSHOT_FATAL", message[:2000]
        if "exit" in lower or bt._is_generic_exit_message(last_error):
            return "KOPIA_PROCESS_DIED", message[:2000]
        return "AGENT_BACKUP_FAILED", message[:2000]
    return bt._directory_error(
        type("Outcome", (), {"result": node_task.result, "task": node_task, "timed_out": False})(),
        timed_out=False,
    )


def _get_node_task_for_directory(
    *,
    directory: BackupSourceSnapshotDirectory,
    organization_id: int,
    task_uuid: str,
    node_id: int,
) -> NodeTask | None:
    if directory.node_task_id:
        task = NodeTask.objects.filter(
            pk=directory.node_task_id,
            organization_id=organization_id,
        ).first()
        if task is not None:
            return task
    return _bt()._matching_backup_node_task(
        organization_id=organization_id,
        node_id=node_id,
        task_uuid=task_uuid,
        backup_config_dir_id=directory.backup_config_dir_id,
    )


def _task_result_payload(task: Task) -> dict[str, Any]:
    return dict(task.result_payload) if isinstance(task.result_payload, dict) else {}


def _save_task_result_payload(task: Task, payload: dict[str, Any]) -> None:
    task.result_payload = payload
    task.save(update_fields=["result_payload", "updated_at"])


def _node_capabilities(node: Node) -> set[str]:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory") if isinstance(metadata.get("inventory"), dict) else {}
    values = inventory.get("capabilities", metadata.get("capabilities", []))
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(value or "").strip() for value in values if str(value or "").strip()}


def _backup_protocol(*, task: Task, node: Node) -> str:
    payload = _task_result_payload(task)
    selected = str(payload.get("backup_protocol") or "").strip()
    if selected in {_BACKUP_PROTOCOL_LEGACY, _BACKUP_PROTOCOL_PREPARED}:
        return selected
    has_legacy_work = NodeTask.objects.filter(
        organization_id=task.organization_id,
        kind="backup.run",
        correlation_type=protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
        correlation_id=str(task.task_uuid),
    ).exists()
    selected = _BACKUP_PROTOCOL_LEGACY
    if (
        not has_legacy_work
        and protection_conf.PROTECTION_BACKUP_PREPARED_SNAPSHOT_ENABLED
        and _BACKUP_PREPARED_CAPABILITY in _node_capabilities(node)
    ):
        selected = _BACKUP_PROTOCOL_PREPARED
    payload["backup_protocol"] = selected
    _save_task_result_payload(task, payload)
    return selected


def _policy_prepare_attempts(
    *,
    task: Task,
    node_id: int,
    backup_config_dir_id: int,
) -> list[NodeTask]:
    return list(
        NodeTask.objects.filter(
            organization_id=task.organization_id,
            node_id=node_id,
            kind="repository.policy.apply",
            correlation_type=protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE,
            correlation_id=str(task.task_uuid),
            payload__backup_config_dir_id=int(backup_config_dir_id),
        ).order_by("created_at", "id")
    )


def _clear_policy_retry_schedule(*, task: Task, backup_config_dir_id: int) -> None:
    payload = _task_result_payload(task)
    schedules = payload.get("policy_prepare_retries")
    if not isinstance(schedules, dict) or str(backup_config_dir_id) not in schedules:
        return
    schedules = dict(schedules)
    schedules.pop(str(backup_config_dir_id), None)
    if schedules:
        payload["policy_prepare_retries"] = schedules
    else:
        payload.pop("policy_prepare_retries", None)
    _save_task_result_payload(task, payload)


def _schedule_policy_retry(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    backup_config_dir_id: int,
    attempt_count: int,
    delay_seconds: int,
) -> None:
    payload = _task_result_payload(task)
    schedules = payload.get("policy_prepare_retries")
    schedules = dict(schedules) if isinstance(schedules, dict) else {}
    key = str(backup_config_dir_id)
    existing = schedules.get(key)
    if isinstance(existing, dict) and int(existing.get("attempt_count") or 0) == attempt_count:
        return
    retry_at = timezone.now() + timedelta(seconds=max(0, int(delay_seconds)))
    schedules[key] = {
        "attempt_count": attempt_count,
        "retry_at": retry_at.isoformat(),
    }
    payload["policy_prepare_retries"] = schedules
    _save_task_result_payload(task, payload)

    def _enqueue() -> None:
        from apps.protection.tasks.backup import advance_backup_task

        advance_backup_task.apply_async(
            kwargs={
                "organization_id": task.organization_id,
                "task_uuid": str(task.task_uuid),
                "source_snapshot_id": source_snapshot.id,
            },
            countdown=max(0, int(delay_seconds)),
        )

    transaction.on_commit(_enqueue)


def _policy_retry_due(*, task: Task, backup_config_dir_id: int, attempt_count: int) -> bool:
    payload = _task_result_payload(task)
    schedules = payload.get("policy_prepare_retries")
    entry = schedules.get(str(backup_config_dir_id)) if isinstance(schedules, dict) else None
    if not isinstance(entry, dict) or int(entry.get("attempt_count") or 0) != attempt_count:
        return False
    retry_at = parse_datetime(str(entry.get("retry_at") or ""))
    return retry_at is None or retry_at <= timezone.now()


def _repository_server_state(task: Task) -> dict[str, Any] | None:
    state = _task_result_payload(task).get("repository_server")
    return state if isinstance(state, dict) else None


def _repository_probe_state(task: Task) -> dict[str, Any] | None:
    state = _task_result_payload(task).get("repository_probe")
    return state if isinstance(state, dict) else None


def _repository_public_host(*, repository: Repository, node: Node) -> tuple[str, str]:
    return explicit_repository_server_host(repository=repository, node=node)


def _repository_server_username(*, task: Task, repository_node: Node) -> str:
    return f"hfl-backup-{task.id}@hfl-proxy-{repository_node.id}".lower()


def _repository_server_payload_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": result.get("repository_id"),
        "type": "kopia_server",
        "url": str(result.get("server_url") or result.get("url") or "").strip(),
        "username": str(result.get("username") or "").strip(),
        "password": str(result.get("password") or "").strip(),
        "server_cert_fingerprint": str(result.get("server_cert_fingerprint") or "").strip(),
        "kopia_password": str(result.get("kopia_password") or "").strip(),
        "session_id": str(result.get("session_id") or "").strip(),
    }


def _repository_server_start_result_payload(
    *,
    repository: Repository,
    repository_node: Node,
    node_task: NodeTask,
    result: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(result)
    payload["repository_id"] = repository.id
    payload["repository_node_id"] = repository_node.id
    payload["node_task_id"] = str(node_task.id)
    payload["status"] = node_task.status
    return payload


def _repository_payload_for_backup(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    repository: Repository,
    execution_target,
    repository_endpoint_type: str,
) -> dict[str, Any] | None:
    bt = _bt()
    repository_access = resolve_repository_reader(
        repository=repository,
        fallback_node=execution_target.node,
        source_type=execution_target.source_type,
        source_ref_id=execution_target.source_ref_id,
        repository_endpoint_type=repository_endpoint_type,
    )
    if int(repository_access.node.id) == int(execution_target.node.id):
        return bt._repository_runtime_payload(
            repository=repository,
            execution_target=execution_target,
            repository_endpoint_type=repository_endpoint_type,
        )
    if not protection_conf.PROTECTION_PROXY_REPOSITORY_SERVER_ENABLED:
        raise ValidationError(
            {
                "repository_id": (
                    "Proxy-bound repository requires proxy repository server mode, "
                    "but PROTECTION_PROXY_REPOSITORY_SERVER_ENABLED is disabled."
                )
            }
        )
    return _ensure_repository_server_payload(
        task=task,
        repository=repository,
        repository_node=repository_access.node,
        repository_payload=repository_access.repository_payload,
    )


def _ensure_repository_server_payload(
    *,
    task: Task,
    repository: Repository,
    repository_node: Node,
    repository_payload: dict[str, Any],
) -> dict[str, Any] | None:
    state = _repository_server_state(task)
    if state and int(state.get("repository_id") or 0) == int(repository.id):
        node_task_id = str(state.get("node_task_id") or "").strip()
        node_task = (
            NodeTask.objects.filter(
                organization_id=task.organization_id,
                id=node_task_id,
            ).first()
            if node_task_id
            else None
        )
        if node_task is not None:
            if node_task.status == NodeTask.Status.SUCCESS:
                result = node_task.result if isinstance(node_task.result, dict) else {}
                result["kopia_password"] = str(repository_payload.get("kopia_password") or "")
                result_payload = _repository_server_start_result_payload(
                    repository=repository,
                    repository_node=repository_node,
                    node_task=node_task,
                    result=result,
                )
                task_payload = _task_result_payload(task)
                task_payload["repository_server"] = result_payload
                _save_task_result_payload(task, task_payload)
                payload = _repository_server_payload_from_result(result_payload)
                if payload["url"] and payload["username"] and payload["password"]:
                    return payload
                raise ValidationError({"repository_id": "Proxy repository server start returned incomplete connection info."})
            if node_task.status in _NODE_TASK_TERMINAL:
                message = str(node_task.last_error or "").strip()
                if not message and isinstance(node_task.result, dict):
                    message = str(node_task.result.get("error") or "").strip()
                raise ValidationError(
                    {
                        "repository_id": (
                            message
                            or f"Proxy repository server start failed: node_task_id={node_task.id}, status={node_task.status}."
                        )
                    }
                )
            if node_task.status == NodeTask.Status.PENDING:
                _redeliver_pending_node_task_after_commit(node_task=node_task)
            return None

    public_host, public_host_source = _repository_public_host(repository=repository, node=repository_node)
    if not public_host:
        raise ValidationError({"repository_id": "Proxy node has no reachable IP address for Kopia server."})
    session_id = f"backup-{task.task_uuid}-repo-{repository.id}"
    username = _repository_server_username(task=task, repository_node=repository_node)
    password = secrets.token_urlsafe(24)
    start_payload = {
        "session_id": session_id,
        "username": username,
        "password": password,
        "public_host": public_host,
        "public_host_source": public_host_source,
        "repository": repository_payload,
    }
    handle = run_agent_task_async(
        organization_id=task.organization_id,
        node_id=repository_node.id,
        kind="repository.server.start",
        payload=start_payload,
        correlation_type=protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
        correlation_id=str(task.task_uuid),
    )
    result = {
        "repository_id": repository.id,
        "repository_node_id": repository_node.id,
        "node_task_id": str(handle.task.id),
        "status": handle.task.status,
        "session_id": session_id,
        "public_host": public_host,
        "public_host_source": public_host_source,
    }
    task_payload = _task_result_payload(task)
    task_payload["repository_server"] = result
    _save_task_result_payload(task, task_payload)
    append_task_step_event(
        task=task,
        step_name="kopia_snapshot",
        message="Proxy repository server started",
        metadata={
            "repository_id": repository.id,
            "repository_node_id": repository_node.id,
            "node_task_id": str(handle.task.id),
            "session_id": session_id,
            "public_host": public_host,
            "public_host_source": public_host_source,
            "object_name": repository.name,
        },
    )
    return None


def _stop_repository_server_for_task(*, task: Task) -> None:
    state = _repository_server_state(task)
    if not state:
        return
    session_id = str(state.get("session_id") or "").strip()
    node_id = int(state.get("repository_node_id") or 0)
    if not session_id or not node_id:
        return
    try:
        run_agent_task_async(
            organization_id=task.organization_id,
            node_id=node_id,
            kind="repository.server.stop",
            payload={"session_id": session_id},
            correlation_type=protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
            correlation_id=str(task.task_uuid),
        )
    except Exception:
        logger.exception("failed to dispatch repository server cleanup task_uuid=%s", task.task_uuid)


def _ensure_source_repository_probe(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    execution_target,
    repository: Repository,
    repository_payload: dict[str, Any],
) -> bool:
    if str(repository_payload.get("type") or "") != "kopia_server":
        return True
    state = _repository_probe_state(task)
    if (
        state
        and int(state.get("repository_id") or 0) == int(repository.id)
        and int(state.get("source_node_id") or 0) == int(execution_target.node.id)
    ):
        node_task_id = str(state.get("node_task_id") or "").strip()
        node_task = (
            NodeTask.objects.filter(
                organization_id=task.organization_id,
                id=node_task_id,
            ).first()
            if node_task_id
            else None
        )
        if node_task is not None:
            if node_task.status == NodeTask.Status.SUCCESS:
                return True
            if node_task.status in _NODE_TASK_TERMINAL:
                message = str(node_task.last_error or "").strip()
                if isinstance(node_task.result, dict):
                    bt = _bt()
                    message = bt.extract_kopia_failure_message(node_task.result, last_error=message)
                raise ValidationError(
                    {
                        "repository_id": (
                            message
                            or f"Source agent cannot connect to proxy repository server: node_task_id={node_task.id}, status={node_task.status}."
                        )
                    }
                )
            if node_task.status == NodeTask.Status.PENDING:
                _redeliver_pending_node_task_after_commit(node_task=node_task)
            return False

    handle = run_agent_task_async(
        organization_id=task.organization_id,
        node_id=execution_target.node.id,
        kind="repo.status",
        payload={
            "repository": repository_payload,
            "source_snapshot_id": source_snapshot.id,
            "probe": "proxy_repository_server",
        },
        correlation_type=protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
        correlation_id=str(task.task_uuid),
    )
    task_payload = _task_result_payload(task)
    task_payload["repository_probe"] = {
        "repository_id": repository.id,
        "source_node_id": execution_target.node.id,
        "node_task_id": str(handle.task.id),
        "status": handle.task.status,
        "server_url": str(repository_payload.get("url") or ""),
    }
    _save_task_result_payload(task, task_payload)
    append_task_step_event(
        task=task,
        step_name="kopia_snapshot",
        message="Source repository connectivity probe started",
        metadata={
            "repository_id": repository.id,
            "source_node_id": execution_target.node.id,
            "node_task_id": str(handle.task.id),
            "server_url": str(repository_payload.get("url") or ""),
            "object_name": repository.name,
        },
    )
    return False


def _mark_policy_prepare_failed(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory_row: BackupSourceSnapshotDirectory,
    node_task: NodeTask,
) -> None:
    error_code, error_message = _node_task_error_code(node_task)
    if (
        error_code != "POLICY_APPLY_FAILED"
        and error_code not in _DELIVERY_DIAGNOSTIC_MESSAGES
    ):
        error_code = "POLICY_APPLY_FAILED"
    record_source_snapshot_directory_result(
        source_snapshot=source_snapshot,
        backup_config_dir_id=directory_row.backup_config_dir_id,
        source_path=directory_row.source_path,
        path_type=directory_row.path_type,
        display_name=directory_row.display_name,
        repository_id=directory_row.repository_id,
        status=BackupSourceSnapshotDirectory.Status.FAILED,
        error_code=error_code,
        error_message=error_message or "Kopia policy preparation failed.",
    )
    append_task_step_event(
        task=task,
        step_name="kopia_snapshot",
        level=TaskEvent.Level.ERROR,
        message="Directory policy preparation failed",
        metadata={
            "backup_config_dir_id": directory_row.backup_config_dir_id,
            "node_task_id": str(node_task.id),
            "attempt_count": len(
                _policy_prepare_attempts(
                    task=task,
                    node_id=node_task.node_id,
                    backup_config_dir_id=directory_row.backup_config_dir_id,
                )
            ),
            "error_code": error_code,
            "error_message": error_message,
            "source_path": directory_row.source_path,
            "object_name": directory_row.source_path,
        },
    )


def _ensure_directory_policy_prepared(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory_row: BackupSourceSnapshotDirectory,
    execution_target,
    repository_payload: dict[str, Any],
    file_filter_payload: dict[str, Any] | None,
    backup_policy_payload: dict[str, Any] | None,
    compression_payload: dict[str, Any],
    agent_source_path: str,
) -> str:
    attempts = _policy_prepare_attempts(
        task=task,
        node_id=execution_target.node.id,
        backup_config_dir_id=directory_row.backup_config_dir_id,
    )
    latest = attempts[-1] if attempts else None
    if latest is not None and latest.status == NodeTask.Status.SUCCESS:
        _clear_policy_retry_schedule(
            task=task,
            backup_config_dir_id=directory_row.backup_config_dir_id,
        )
        return "prepared"
    if latest is not None and latest.status not in _NODE_TASK_TERMINAL:
        if latest.status == NodeTask.Status.PENDING:
            _redeliver_pending_node_task_after_commit(node_task=latest)
        return "waiting"

    max_attempts = 1 + max(0, protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_MAX_RETRIES)
    if latest is not None:
        if len(attempts) >= max_attempts:
            _clear_policy_retry_schedule(
                task=task,
                backup_config_dir_id=directory_row.backup_config_dir_id,
            )
            _mark_policy_prepare_failed(
                task=task,
                source_snapshot=source_snapshot,
                directory_row=directory_row,
                node_task=latest,
            )
            return "failed"
        delays = protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_RETRY_DELAYS
        delay = int(delays[min(len(attempts) - 1, len(delays) - 1)]) if delays else 0
        if not _policy_retry_due(
            task=task,
            backup_config_dir_id=directory_row.backup_config_dir_id,
            attempt_count=len(attempts),
        ):
            _schedule_policy_retry(
                task=task,
                source_snapshot=source_snapshot,
                backup_config_dir_id=directory_row.backup_config_dir_id,
                attempt_count=len(attempts),
                delay_seconds=delay,
            )
            return "waiting"
        _clear_policy_retry_schedule(
            task=task,
            backup_config_dir_id=directory_row.backup_config_dir_id,
        )

    bt = _bt()
    payload = bt._agent_backup_payload(
        source_path=agent_source_path,
        backup_config_dir_id=directory_row.backup_config_dir_id,
        repository_payload=repository_payload,
        nas_payload=execution_target.nas_payload,
        file_filter_payload=file_filter_payload,
        backup_policy_payload=backup_policy_payload,
        compression_payload=compression_payload,
    )
    payload["policy_attempt"] = len(attempts) + 1
    handle = run_agent_task_async(
        organization_id=task.organization_id,
        node_id=execution_target.node.id,
        kind="repository.policy.apply",
        payload=payload,
        correlation_type=protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE,
        correlation_id=str(task.task_uuid),
    )
    append_task_step_event(
        task=task,
        step_name="kopia_snapshot",
        message="Preparing directory policy",
        metadata={
            "backup_config_dir_id": directory_row.backup_config_dir_id,
            "source_path": directory_row.source_path,
            "node_task_id": str(handle.task.id),
            "attempt": len(attempts) + 1,
            "object_name": directory_row.source_path,
        },
    )
    return "waiting"


def _prepare_directory_policies(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directories,
    execution_target,
    repository: Repository,
    repository_payload: dict[str, Any],
    file_filter_payload: dict[str, Any] | None,
    backup_policy_payload: dict[str, Any] | None,
    compression_payload: dict[str, Any],
) -> bool:
    bt = _bt()
    for directory in directories:
        source_path = bt._clean_path(directory.path)
        path_type = str(getattr(directory, "path_type", "") or "unknown")
        agent_source_path = source_path
        if execution_target.root_path:
            from apps.source.services.internal.nas_share_path import to_mount_path

            agent_source_path = to_mount_path(execution_target.root_path, source_path)
        directory_row = BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=source_snapshot,
            backup_config_dir_id=directory.id,
        ).first()
        if directory_row is None:
            directory_row = record_source_snapshot_directory_result(
                source_snapshot=source_snapshot,
                backup_config_dir_id=directory.id,
                source_path=source_path,
                path_type=path_type,
                display_name=directory.display_name,
                repository_id=repository.id,
                status=BackupSourceSnapshotDirectory.Status.PENDING,
            )
            append_task_step_event(
                task=task,
                step_name="kopia_snapshot",
                message="Starting directory snapshot",
                metadata={
                    "backup_config_dir_id": directory.id,
                    "source_path": source_path,
                    "object_name": source_path,
                },
            )
        if directory_row.status in _DIRECTORY_TERMINAL:
            continue
        if execution_target.root_path and not bt._is_subpath(execution_target.root_path, agent_source_path):
            record_source_snapshot_directory_result(
                source_snapshot=source_snapshot,
                backup_config_dir_id=directory.id,
                source_path=source_path,
                path_type=path_type,
                display_name=directory.display_name,
                repository_id=repository.id,
                status=BackupSourceSnapshotDirectory.Status.FAILED,
                error_code="SOURCE_PATH_FORBIDDEN",
                error_message="Backup config source path is outside the mounted NAS path.",
            )
            continue
        state = _ensure_directory_policy_prepared(
            task=task,
            source_snapshot=source_snapshot,
            directory_row=directory_row,
            execution_target=execution_target,
            repository_payload=repository_payload,
            file_filter_payload=file_filter_payload,
            backup_policy_payload=backup_policy_payload,
            compression_payload=compression_payload,
            agent_source_path=agent_source_path,
        )
        if state == "waiting":
            return False
    return True


def _dispatch_directory_backup(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory_row: BackupSourceSnapshotDirectory,
    config_directory,
    execution_target,
    repository,
    repository_payload: dict[str, Any],
    file_filter_payload: dict[str, Any] | None,
    backup_policy_payload: dict[str, Any] | None,
    compression_payload: dict[str, Any],
    agent_source_path: str,
    source_path: str,
    task_kind: str = "backup.run",
) -> None:
    bt = _bt()
    operation_id = f"{task.task_uuid}-{directory_row.backup_config_dir_id}"
    operation_attempt = int(directory_row.retry_count or 0) + 1
    existing = _get_node_task_for_directory(
        directory=directory_row,
        organization_id=task.organization_id,
        task_uuid=str(task.task_uuid),
        node_id=execution_target.node.id,
    )
    if existing is not None and existing.status == NodeTask.Status.SUCCESS:
        ensure_snapshot_repository_locator(
            directory=directory_row,
            repository=repository,
            writer_node_id=existing.node_id,
        )
        snapshot_id, size_bytes, file_count, dir_count, stats = bt._extract_snapshot_metrics(
            existing.result if isinstance(existing.result, dict) else {}
        )
        if snapshot_id:
            record_source_snapshot_directory_result(
                source_snapshot=source_snapshot,
                backup_config_dir_id=directory_row.backup_config_dir_id,
                source_path=directory_row.source_path or source_path,
                path_type=directory_row.path_type,
                display_name=directory_row.display_name,
                repository_id=directory_row.repository_id,
                status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
                kopia_snapshot_id=snapshot_id,
                size_bytes=size_bytes,
                file_count=file_count,
                dir_count=dir_count,
                stats=stats,
            )
            directory_row.node_task_id = existing.id
            directory_row.save(update_fields=["node_task_id", "updated_at"])
            append_task_step_event(
                task=task,
                step_name="kopia_snapshot",
                message="Directory snapshot created",
                metadata={
                    "backup_config_dir_id": directory_row.backup_config_dir_id,
                    "source_path": directory_row.source_path or source_path,
                    "node_task_id": str(existing.id),
                    "kopia_snapshot_id": snapshot_id,
                    "object_id": snapshot_id,
                    "object_name": directory_row.source_path or source_path,
                },
            )
            return
        directory_row.status = BackupSourceSnapshotDirectory.Status.RUNNING
        directory_row.node_task_id = existing.id
        directory_row.save(update_fields=["status", "node_task_id", "updated_at"])
        return
    if existing is not None and existing.status not in _NODE_TASK_TERMINAL:
        ensure_snapshot_repository_locator(
            directory=directory_row,
            repository=repository,
            writer_node_id=existing.node_id,
        )
        directory_row.status = (
            BackupSourceSnapshotDirectory.Status.RUNNING
            if existing.status == NodeTask.Status.RUNNING
            else BackupSourceSnapshotDirectory.Status.DISPATCHING
        )
        directory_row.node_task_id = existing.id
        if not directory_row.dispatched_at:
            directory_row.dispatched_at = existing.dispatched_at or timezone.now()
        directory_row.save(
            update_fields=["status", "node_task_id", "dispatched_at", "updated_at"]
        )
        if existing.status == NodeTask.Status.PENDING:
            _redeliver_pending_node_task_after_commit(node_task=existing)
        return

    ensure_snapshot_repository_locator(
        directory=directory_row,
        repository=repository,
        writer_node_id=execution_target.node.id,
    )
    handle = run_agent_task_async(
        organization_id=task.organization_id,
        node_id=execution_target.node.id,
        kind=task_kind,
        payload=bt._agent_backup_payload(
            source_path=agent_source_path,
            backup_config_dir_id=config_directory.id,
            repository_payload=repository_payload,
            nas_payload=execution_target.nas_payload,
            file_filter_payload=file_filter_payload if task_kind == "backup.run" else None,
            backup_policy_payload=backup_policy_payload if task_kind == "backup.run" else None,
            compression_payload=compression_payload if task_kind == "backup.run" else None,
            operation_id=operation_id,
            operation_attempt=operation_attempt,
        ),
        correlation_type=protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
        correlation_id=str(task.task_uuid),
    )
    now = timezone.now()
    directory_row.status = BackupSourceSnapshotDirectory.Status.DISPATCHING
    directory_row.node_task_id = handle.task.id
    directory_row.dispatched_at = now
    directory_row.source_path = source_path
    directory_row.save(
        update_fields=[
            "status",
            "node_task_id",
            "dispatched_at",
            "source_path",
            "updated_at",
        ]
    )
    append_task_step_event(
        task=task,
        step_name="kopia_snapshot",
        message=(
            "Dispatching prepared directory snapshot to agent"
            if task_kind == "backup.snapshot.create"
            else "Dispatching directory backup to agent"
        ),
        metadata={
            "backup_config_dir_id": config_directory.id,
            "source_path": source_path,
            "node_id": execution_target.node.id,
            "node_task_id": str(handle.task.id),
            "repository_id": repository.id,
            "repository_type": repository.repo_type,
            "orchestrator": True,
            "task_kind": task_kind,
            "operation_id": operation_id,
            "operation_attempt": operation_attempt,
            "object_name": source_path,
        },
    )


def _maybe_adopt_late_success(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory_row: BackupSourceSnapshotDirectory,
    node_task: NodeTask,
) -> bool:
    if directory_row.status != BackupSourceSnapshotDirectory.Status.FAILED:
        return False
    if node_task.status != NodeTask.Status.SUCCESS:
        return False
    if directory_row.error_code not in protection_conf.LATE_SUCCESS_ADOPT_ERROR_CODES:
        return False
    previous_error_code = directory_row.error_code
    adopt_window = protection_conf.PROTECTION_BACKUP_LATE_SUCCESS_ADOPT_SECONDS
    if adopt_window > 0:
        failed_at = directory_row.updated_at
        if failed_at and (timezone.now() - failed_at).total_seconds() > adopt_window:
            return False
    newer = Task.objects.filter(
        organization_id=task.organization_id,
        task_type=Task.Type.BACKUP,
        status__in={Task.Status.PENDING, Task.Status.RUNNING},
        created_at__gt=task.created_at,
        request_payload__backup_config_id=source_snapshot.backup_config_id,
    ).exists()
    if newer:
        append_task_step_event(
            task=task,
            step_name="kopia_snapshot",
            level=TaskEvent.Level.WARN,
            message="Late backup result ignored due to newer backup task",
            metadata={
                "backup_config_dir_id": directory_row.backup_config_dir_id,
                "node_task_id": str(node_task.id),
                "source_path": directory_row.source_path,
                "object_name": directory_row.source_path,
            },
        )
        return False
    bt = _bt()
    snapshot_id, size_bytes, file_count, dir_count, stats = bt._extract_snapshot_metrics(
        node_task.result if isinstance(node_task.result, dict) else {}
    )
    if not snapshot_id:
        return False
    repository = Repository.objects.filter(
        organization_id=directory_row.organization_id,
        id=directory_row.repository_id,
    ).first()
    if repository is not None:
        ensure_snapshot_repository_locator(
            directory=directory_row,
            repository=repository,
            writer_node_id=node_task.node_id,
        )
    record_source_snapshot_directory_result(
        source_snapshot=source_snapshot,
        backup_config_dir_id=directory_row.backup_config_dir_id,
        source_path=directory_row.source_path,
        path_type=directory_row.path_type,
        display_name=directory_row.display_name,
        repository_id=directory_row.repository_id,
        status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        kopia_snapshot_id=snapshot_id,
        size_bytes=size_bytes,
        file_count=file_count,
        dir_count=dir_count,
        stats=stats,
    )
    directory_row.adopted_late_result = True
    directory_row.save(update_fields=["adopted_late_result", "updated_at"])
    append_task_step_event(
        task=task,
        step_name="kopia_snapshot",
        message="Late backup result adopted",
        metadata={
            "backup_config_dir_id": directory_row.backup_config_dir_id,
            "node_task_id": str(node_task.id),
            "kopia_snapshot_id": snapshot_id,
            "previous_error_code": previous_error_code,
            "source_path": directory_row.source_path,
            "object_id": snapshot_id,
            "object_name": directory_row.source_path,
        },
    )
    return True


def _handle_directory_stall(
    *,
    task: Task,
    directory_row: BackupSourceSnapshotDirectory,
    node_task: NodeTask,
    node_online: bool,
) -> bool:
    """Returns True if directory was failed."""
    if not node_online:
        return False
    now = timezone.now()
    reference = directory_row.last_substantive_progress_at or directory_row.dispatched_at or node_task.last_progress_at
    if reference is None:
        return False
    elapsed = (now - reference).total_seconds()
    warn_seconds = protection_conf.PROTECTION_BACKUP_SUBSTANTIVE_STALL_WARN_SECONDS
    fail_seconds = protection_conf.PROTECTION_BACKUP_SUBSTANTIVE_STALL_FAIL_SECONDS
    if elapsed >= warn_seconds and directory_row.stall_warned_at is None:
        directory_row.stall_warned_at = now
        directory_row.save(update_fields=["stall_warned_at", "updated_at"])
        append_task_step_event(
            task=task,
            step_name="kopia_snapshot",
            level=TaskEvent.Level.WARN,
            message="Backup progress stalled",
            metadata={
                "backup_config_dir_id": directory_row.backup_config_dir_id,
                "source_path": directory_row.source_path,
                "elapsed_seconds": int(elapsed),
                "object_name": directory_row.source_path,
            },
        )
    if elapsed < fail_seconds:
        return False
    if directory_row.cancel_requested_at is None:
        directory_row.cancel_requested_at = now
        directory_row.save(update_fields=["cancel_requested_at", "updated_at"])
        cancel_agent_task(task_id=node_task.id, reason="kopia progress stall")
        return False
    grace = protection_conf.PROTECTION_BACKUP_CANCEL_GRACE_SECONDS
    if (now - directory_row.cancel_requested_at).total_seconds() < grace:
        return False
    error_code = "KOPIA_PROGRESS_STALL"
    error_message = f"No substantive backup progress for {int(elapsed)} seconds."
    record_source_snapshot_directory_result(
        source_snapshot=directory_row.source_snapshot,
        backup_config_dir_id=directory_row.backup_config_dir_id,
        source_path=directory_row.source_path,
        path_type=directory_row.path_type,
        display_name=directory_row.display_name,
        repository_id=directory_row.repository_id,
        status=BackupSourceSnapshotDirectory.Status.FAILED,
        error_code=error_code,
        error_message=error_message,
    )
    append_task_step_event(
        task=task,
        step_name="kopia_snapshot",
        level=TaskEvent.Level.ERROR,
        message="Directory backup failed",
        metadata={
            "backup_config_dir_id": directory_row.backup_config_dir_id,
            "source_path": directory_row.source_path,
            "node_task_id": str(node_task.id),
            "error_code": error_code,
            "error_message": error_message,
            "object_name": directory_row.source_path,
        },
    )
    return True


_STALE_DIRECTORY_FAILURE_CODES = frozenset(
    {
        "AGENT_RESTARTED",
        "AGENT_OFFLINE",
        "AGENT_TIMEOUT",
        "WATCHDOG_STALL",
        "RESULT_ACK_TIMEOUT",
    }
)


def _directory_failure_needs_refresh(
    *,
    directory_row: BackupSourceSnapshotDirectory,
    node_task: NodeTask,
) -> bool:
    if directory_row.status != BackupSourceSnapshotDirectory.Status.FAILED:
        return False
    if node_task.status not in _NODE_TASK_TERMINAL:
        return False
    bt = _bt()
    stored_code = str(directory_row.error_code or "").strip()
    stored_message = str(directory_row.error_message or "").strip()
    current_code, current_message = _node_task_error_code(node_task)
    if stored_code in _STALE_DIRECTORY_FAILURE_CODES and current_code != stored_code:
        return True
    if bt._is_generic_exit_message(stored_message):
        return True
    if not stored_message and current_message:
        return True
    if (
        current_message
        and stored_message != current_message
        and bt._is_generic_exit_message(stored_message)
    ):
        return True
    return False


def _patch_latest_directory_failure_event(
    *,
    task: Task,
    directory_row: BackupSourceSnapshotDirectory,
    error_code: str,
    error_message: str,
) -> None:
    from apps.task.models import TaskEvent

    node_task_id = str(directory_row.node_task_id or "")
    events = (
        TaskEvent.objects.filter(
            task=task,
            message="Directory backup failed",
            level=TaskEvent.Level.ERROR,
        )
        .order_by("-seq", "-id")[:20]
    )
    for event in events:
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        matches_dir = metadata.get("backup_config_dir_id") == directory_row.backup_config_dir_id
        matches_task = node_task_id and str(metadata.get("node_task_id") or "") == node_task_id
        if not matches_dir and not matches_task:
            continue
        stored_message = str(metadata.get("error_message") or "").strip()
        stored_code = str(metadata.get("error_code") or "").strip()
        if stored_code == error_code and stored_message == error_message:
            return
        event.metadata = {
            **metadata,
            "error_code": error_code,
            "error_message": error_message,
            "object_name": directory_row.source_path,
        }
        event.save(update_fields=["metadata"])
        return


def _maybe_refresh_stale_directory_failure(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory_row: BackupSourceSnapshotDirectory,
    node_task: NodeTask,
    directory_index: int,
    total_dirs: int,
    node_online: bool,
) -> bool:
    del directory_index, total_dirs, node_online
    if not _directory_failure_needs_refresh(
        directory_row=directory_row,
        node_task=node_task,
    ):
        return False
    error_code, error_message = _node_task_error_code(node_task)
    record_source_snapshot_directory_result(
        source_snapshot=source_snapshot,
        backup_config_dir_id=directory_row.backup_config_dir_id,
        source_path=directory_row.source_path,
        path_type=directory_row.path_type,
        display_name=directory_row.display_name,
        repository_id=directory_row.repository_id,
        status=BackupSourceSnapshotDirectory.Status.FAILED,
        error_code=error_code,
        error_message=error_message,
    )
    _patch_latest_directory_failure_event(
        task=task,
        directory_row=directory_row,
        error_code=error_code,
        error_message=error_message,
    )
    return True


def _backfill_terminal_backup_error_messages(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
) -> None:
    if task.status != Task.Status.FAILED:
        return
    bt = _bt()
    failed_rows = list(
        BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=source_snapshot,
            status=BackupSourceSnapshotDirectory.Status.FAILED,
        ).order_by("id")
    )
    primary = next(
        (row for row in failed_rows if str(row.error_message or "").strip()),
        None,
    )
    if primary is None:
        return
    message = str(primary.error_message).strip()
    code = str(primary.error_code or "").strip()
    task_updates: list[str] = []
    if message and (
        bt._is_generic_exit_message(task.error_message or "")
        or not str(task.error_message or "").strip()
    ):
        task.error_code = code or task.error_code
        task.error_message = message
        task_updates.extend(["error_code", "error_message", "updated_at"])
    if task_updates:
        task.save(update_fields=task_updates)
    snapshot_updates: list[str] = []
    if message and (
        bt._is_generic_exit_message(source_snapshot.error_message or "")
        or not str(source_snapshot.error_message or "").strip()
    ):
        source_snapshot.error_code = code or source_snapshot.error_code
        source_snapshot.error_message = message
        snapshot_updates.extend(["error_code", "error_message", "updated_at"])
    if snapshot_updates:
        source_snapshot.save(update_fields=snapshot_updates)


def _fail_directory_due_to_offline(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory_row: BackupSourceSnapshotDirectory,
    node_task: NodeTask,
) -> None:
    from apps.node.services.internal.task_offline_reconcile import fail_node_task_offline

    if node_task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
        fail_node_task_offline(node_task=node_task)
        node_task.refresh_from_db()
    if node_task.status == NodeTask.Status.SUCCESS:
        return
    error_code = "AGENT_OFFLINE"
    error_message = str(node_task.last_error or "Agent went offline during backup.")
    record_source_snapshot_directory_result(
        source_snapshot=source_snapshot,
        backup_config_dir_id=directory_row.backup_config_dir_id,
        source_path=directory_row.source_path,
        path_type=directory_row.path_type,
        display_name=directory_row.display_name,
        repository_id=directory_row.repository_id,
        status=BackupSourceSnapshotDirectory.Status.FAILED,
        error_code=error_code,
        error_message=error_message,
    )
    append_task_step_event(
        task=task,
        step_name="kopia_snapshot",
        level=TaskEvent.Level.ERROR,
        message="Directory backup failed",
        metadata={
            "backup_config_dir_id": directory_row.backup_config_dir_id,
            "source_path": directory_row.source_path,
            "node_task_id": str(node_task.id),
            "node_task_status": node_task.status,
            "error_code": error_code,
            "error_message": error_message,
            "object_name": directory_row.source_path,
        },
    )


def _observe_running_directory(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory_row: BackupSourceSnapshotDirectory,
    node_task: NodeTask,
    directory_index: int,
    total_dirs: int,
    node_online: bool,
    execution_node: Node | None = None,
) -> None:
    bt = _bt()
    if _maybe_adopt_late_success(
        task=task,
        source_snapshot=source_snapshot,
        directory_row=directory_row,
        node_task=node_task,
    ):
        return
    progress = {}
    result = node_task.result if isinstance(node_task.result, dict) else {}
    last_progress = result.get("last_progress")
    if isinstance(last_progress, dict):
        progress = last_progress
    from apps.protection.services.progress.backup_runtime import (
        sync_backup_task_progress,
        update_directory_progress_snapshot,
    )

    directory_row = update_directory_progress_snapshot(
        directory=directory_row,
        progress=progress or None,
        node_task=node_task,
    )
    if progress or str(directory_row.status or "").lower() in {"pending", "dispatching", "running", "creating"}:
        sync_backup_task_progress(task=task, source_snapshot=source_snapshot)
    if execution_node is not None:
        from apps.node.services.internal.task_offline_reconcile import (
            is_node_offline_stale,
            is_node_reconnecting,
            persist_task_execution_state,
        )

        persist_task_execution_state(task=task, node=execution_node)
        if node_task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
            if is_node_reconnecting(execution_node):
                return
            if is_node_offline_stale(execution_node):
                _fail_directory_due_to_offline(
                    task=task,
                    source_snapshot=source_snapshot,
                    directory_row=directory_row,
                    node_task=node_task,
                )
                return
            if not node_online:
                return
    if node_task.status in _NODE_TASK_TERMINAL:
        if node_task.status == NodeTask.Status.SUCCESS:
            snapshot_id, size_bytes, file_count, dir_count, stats = bt._extract_snapshot_metrics(
                node_task.result if isinstance(node_task.result, dict) else {}
            )
            if not snapshot_id:
                record_source_snapshot_directory_result(
                    source_snapshot=source_snapshot,
                    backup_config_dir_id=directory_row.backup_config_dir_id,
                    source_path=directory_row.source_path,
                    path_type=directory_row.path_type,
                    display_name=directory_row.display_name,
                    repository_id=directory_row.repository_id,
                    status=BackupSourceSnapshotDirectory.Status.FAILED,
                    error_code="KOPIA_SNAPSHOT_ID_MISSING",
                    error_message="Kopia snapshot creation succeeded but no snapshot id was returned.",
                    stats=stats,
                )
            else:
                record_source_snapshot_directory_result(
                    source_snapshot=source_snapshot,
                    backup_config_dir_id=directory_row.backup_config_dir_id,
                    source_path=directory_row.source_path,
                    path_type=directory_row.path_type,
                    display_name=directory_row.display_name,
                    repository_id=directory_row.repository_id,
                    status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
                    kopia_snapshot_id=snapshot_id,
                    size_bytes=size_bytes,
                    file_count=file_count,
                    dir_count=dir_count,
                    stats=stats,
                )
                append_task_step_event(
                    task=task,
                    step_name="kopia_snapshot",
                    message="Directory snapshot created",
                    metadata={
                        "backup_config_dir_id": directory_row.backup_config_dir_id,
                        "source_path": directory_row.source_path,
                        "node_task_id": str(node_task.id),
                        "kopia_snapshot_id": snapshot_id,
                        "object_id": snapshot_id,
                        "object_name": directory_row.source_path,
                    },
                )
        else:
            error_code, error_message = _node_task_error_code(node_task)
            if (
                error_code not in _DELIVERY_DIAGNOSTIC_MESSAGES
                and not node_online
                and node_task.status in {
                    NodeTask.Status.FAILED,
                    NodeTask.Status.TIMEOUT,
                }
            ):
                error_code = "AGENT_OFFLINE"
                error_message = str(
                    node_task.last_error or "Agent went offline during backup."
                )
            if (
                error_code == "AGENT_RESTARTED"
                and node_task.status != NodeTask.Status.CANCELED
                and int(directory_row.retry_count or 0) < 1
            ):
                retry_node = Node.objects.filter(pk=node_task.node_id).only("metadata").first()
                supports_safe_retry = (
                    retry_node is not None
                    and _BACKUP_OPERATION_RECONCILE_CAPABILITY
                    in _node_capabilities(retry_node)
                )
                if not supports_safe_retry:
                    grace_seconds = max(
                        0,
                        protection_conf.PROTECTION_BACKUP_CAPABILITY_SYNC_GRACE_SECONDS,
                    )
                    terminal_age = (timezone.now() - node_task.updated_at).total_seconds()
                    if terminal_age < grace_seconds:
                        pending_code = "AGENT_RESTART_CAPABILITY_PENDING"
                        if directory_row.error_code != pending_code:
                            directory_row.error_code = pending_code
                            directory_row.error_message = (
                                "Waiting for the reconnected Agent to report backup retry capabilities."
                            )
                            directory_row.save(
                                update_fields=[
                                    "error_code",
                                    "error_message",
                                    "updated_at",
                                ]
                            )
                            append_task_step_event(
                                task=task,
                                step_name="kopia_snapshot",
                                level=TaskEvent.Level.WARN,
                                message="Waiting for Agent capability sync",
                                metadata={
                                    "backup_config_dir_id": directory_row.backup_config_dir_id,
                                    "previous_node_task_id": str(node_task.id),
                                    "required_capability": _BACKUP_OPERATION_RECONCILE_CAPABILITY,
                                    "source_path": directory_row.source_path,
                                    "object_name": directory_row.source_path,
                                },
                            )
                        return
                    error_code = "AGENT_UPGRADE_REQUIRED"
                    error_message = (
                        "Upgrade the Agent before retrying this backup so an existing "
                        "snapshot can be verified safely."
                    )
                else:
                    directory_row.status = BackupSourceSnapshotDirectory.Status.PENDING
                    directory_row.node_task_id = None
                    directory_row.retry_count = int(directory_row.retry_count or 0) + 1
                    directory_row.error_code = "AGENT_RESTART_RECOVERY_PENDING"
                    directory_row.error_message = error_message
                    directory_row.dispatched_at = None
                    directory_row.last_substantive_progress_at = None
                    directory_row.last_progress_snapshot = {}
                    directory_row.last_progress_sample = {}
                    directory_row.cancel_requested_at = None
                    directory_row.stall_warned_at = None
                    directory_row.save(
                        update_fields=[
                            "status",
                            "node_task_id",
                            "retry_count",
                            "error_code",
                            "error_message",
                            "dispatched_at",
                            "last_substantive_progress_at",
                            "last_progress_snapshot",
                            "last_progress_sample",
                            "cancel_requested_at",
                            "stall_warned_at",
                            "updated_at",
                        ]
                    )
                    append_task_step_event(
                        task=task,
                        step_name="kopia_snapshot",
                        level=TaskEvent.Level.WARN,
                        message="Backup execution retry queued after Agent restart",
                        metadata={
                            "backup_config_dir_id": directory_row.backup_config_dir_id,
                            "previous_node_task_id": str(node_task.id),
                            "retry_count": directory_row.retry_count,
                            "error_code": error_code,
                            "source_path": directory_row.source_path,
                            "object_name": directory_row.source_path,
                        },
                    )
                    return
            if (
                error_code == "KOPIA_POLICY_NOT_FOUND"
                and node_task.status != NodeTask.Status.CANCELED
                and int(directory_row.retry_count or 0) < 1
            ):
                directory_row.status = BackupSourceSnapshotDirectory.Status.PENDING
                directory_row.node_task_id = None
                directory_row.retry_count = int(directory_row.retry_count or 0) + 1
                directory_row.error_code = "KOPIA_POLICY_REPAIR_PENDING"
                directory_row.error_message = error_message
                directory_row.dispatched_at = None
                directory_row.last_substantive_progress_at = None
                directory_row.last_progress_snapshot = {}
                directory_row.last_progress_sample = {}
                directory_row.cancel_requested_at = None
                directory_row.stall_warned_at = None
                directory_row.save(
                    update_fields=[
                        "status",
                        "node_task_id",
                        "retry_count",
                        "error_code",
                        "error_message",
                        "dispatched_at",
                        "last_substantive_progress_at",
                        "last_progress_snapshot",
                        "last_progress_sample",
                        "cancel_requested_at",
                        "stall_warned_at",
                        "updated_at",
                    ]
                )
                append_task_step_event(
                    task=task,
                    step_name="kopia_snapshot",
                    level=TaskEvent.Level.WARN,
                    message="Directory policy repair queued after parallel upload batch",
                    metadata={
                        "backup_config_dir_id": directory_row.backup_config_dir_id,
                        "node_task_id": str(node_task.id),
                        "error_code": error_code,
                        "source_path": directory_row.source_path,
                        "object_name": directory_row.source_path,
                    },
                )
                return
            directory_status = (
                BackupSourceSnapshotDirectory.Status.CANCELLED
                if error_code == "USER_CANCELLED"
                else BackupSourceSnapshotDirectory.Status.FAILED
            )
            record_source_snapshot_directory_result(
                source_snapshot=source_snapshot,
                backup_config_dir_id=directory_row.backup_config_dir_id,
                source_path=directory_row.source_path,
                path_type=directory_row.path_type,
                display_name=directory_row.display_name,
                repository_id=directory_row.repository_id,
                status=directory_status,
                error_code=error_code,
                error_message=error_message,
            )
            append_task_step_event(
                task=task,
                step_name="kopia_snapshot",
                level=TaskEvent.Level.ERROR,
                message="Directory backup failed",
                metadata={
                    "backup_config_dir_id": directory_row.backup_config_dir_id,
                    "source_path": directory_row.source_path,
                    "node_task_id": str(node_task.id),
                    "node_task_status": node_task.status,
                    "error_code": error_code,
                    "error_message": error_message,
                    "object_name": directory_row.source_path,
                },
            )
        return
    if directory_row.status == BackupSourceSnapshotDirectory.Status.DISPATCHING:
        if node_task.status == NodeTask.Status.RUNNING:
            directory_row.status = BackupSourceSnapshotDirectory.Status.RUNNING
            directory_row.save(update_fields=["status", "updated_at"])
    if node_task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
        pending_limit = protection_conf.PROTECTION_BACKUP_DISPATCH_PENDING_SECONDS
        if node_task.status == NodeTask.Status.PENDING and directory_row.dispatched_at:
            if (timezone.now() - directory_row.dispatched_at).total_seconds() > pending_limit:
                _redeliver_pending_node_task_after_commit(node_task=node_task)
        if node_task.status == NodeTask.Status.RUNNING:
            _handle_directory_stall(
                task=task,
                directory_row=directory_row,
                node_task=node_task,
                node_online=node_online,
            )


def _finalize_backup_task(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    organization_id: int,
    total_dirs: int,
) -> dict[str, Any]:
    bt = _bt()
    source_snapshot = refresh_source_snapshot_summary(
        source_snapshot=source_snapshot,
        finished_at=timezone.now(),
    )
    rows = list(
        BackupSourceSnapshotDirectory.objects.filter(source_snapshot_id=source_snapshot.id)
    )
    successful = sum(
        1 for row in rows if row.status == BackupSourceSnapshotDirectory.Status.AVAILABLE
    )
    any_failure = successful < total_dirs
    step_progress, task_progress = bt._backup_success_progress(successful, total_dirs)
    bt._set_step_status(
        task=task,
        step_name="finalize_snapshot",
        status=TaskStep.Status.RUNNING,
        progress=10 if successful == total_dirs else 0,
        task_progress=task_progress,
        current_step="finalize_snapshot",
    )
    task_result = {
        "source_snapshot_id": source_snapshot.id,
        "source_snapshot_status": source_snapshot.status,
        "successful_directory_count": source_snapshot.successful_directory_count,
        "failed_directory_count": source_snapshot.failed_directory_count,
        "directory_count": source_snapshot.directory_count,
        "total_size_bytes": source_snapshot.total_size_bytes,
        "file_count": source_snapshot.file_count,
        "dir_count": source_snapshot.dir_count,
    }
    repository = Repository.objects.filter(
        organization_id=organization_id,
        id=source_snapshot.repository_id,
    ).first()
    if repository is not None:
        try:
            refresh = enqueue_repository_usage_refresh(
                organization_id=organization_id,
                repository_ids=[repository.id],
                force=True,
                trigger="protection.backup.completed",
            )
            task_result["repository_usage_refresh_queued"] = bool(refresh.get("queued"))
            append_task_step_event(
                task=task,
                step_name="finalize_snapshot",
                message="Repository usage refresh queued",
                metadata={
                    "repository_id": repository.id,
                    "queued": bool(refresh.get("queued")),
                    "deduplicated": bool(refresh.get("deduplicated")),
                    "celery_task_id": refresh.get("task_id"),
                    "object_name": repository.name,
                },
            )
        except Exception as exc:
            logger.exception(
                "Failed to queue repository usage refresh after backup for repository_id=%s",
                repository.id,
            )
            append_task_step_event(
                task=task,
                step_name="finalize_snapshot",
                level=TaskEvent.Level.WARN,
                message="Repository usage refresh queue failed",
                metadata={
                    "repository_id": repository.id,
                    "error_message": str(exc)[:1000],
                    "object_name": repository.name,
                },
            )

    _stop_repository_server_for_task(task=task)

    if source_snapshot.status == BackupSourceSnapshot.Status.AVAILABLE and not any_failure:
        bt._set_step_status(
            task=task,
            step_name="kopia_snapshot",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=BACKUP_TRANSFER_END,
        )
        bt._set_step_status(
            task=task,
            step_name="finalize_snapshot",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=100,
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=organization_id,
            status=Task.Status.SUCCESS,
            progress=100,
            result_payload=task_result,
        )
        final_status = Task.Status.SUCCESS
    else:
        failed_directory_rows = [
            row
            for row in rows
            if row.status
            in {
                BackupSourceSnapshotDirectory.Status.FAILED,
                BackupSourceSnapshotDirectory.Status.CANCELLED,
            }
        ]
        error_code = source_snapshot.error_code or "BACKUP_PARTIAL_OR_FAILED"
        error_message = source_snapshot.error_message or (
            "One or more directories failed during backup."
            if source_snapshot.successful_directory_count > 0
            else "Backup failed for all configured directories."
        )
        if failed_directory_rows:
            primary = failed_directory_rows[0]
            if str(primary.error_code or "").strip():
                error_code = str(primary.error_code).strip()
            if str(primary.error_message or "").strip():
                error_message = str(primary.error_message).strip()
        source_snapshot.error_code = error_code
        source_snapshot.error_message = error_message
        source_snapshot.save(update_fields=["error_code", "error_message", "updated_at"])
        bt._set_step_status(
            task=task,
            step_name="kopia_snapshot",
            status=TaskStep.Status.FAILED,
            progress=step_progress,
            task_progress=task_progress,
        )
        bt._set_step_status(
            task=task,
            step_name="finalize_snapshot",
            status=TaskStep.Status.FAILED,
            progress=0,
            task_progress=task_progress,
        )
        append_task_step_event(
            task=task,
            step_name="finalize_snapshot",
            level=TaskEvent.Level.ERROR,
            message="Backup finished with failed directories",
            metadata={
                "source_snapshot_id": source_snapshot.id,
                "source_snapshot_status": source_snapshot.status,
                "successful_directory_count": source_snapshot.successful_directory_count,
                "failed_directory_count": source_snapshot.failed_directory_count,
                "object_name": source_snapshot.snapshot_uid,
            },
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=organization_id,
            status=Task.Status.FAILED,
            progress=task_progress,
            result_payload=task_result,
            error_code=error_code,
            error_message=error_message,
        )
        final_status = Task.Status.FAILED
    logger.info(
        "backup orchestrator finished task_uuid=%s source_snapshot_id=%s status=%s",
        task.task_uuid,
        source_snapshot.id,
        final_status,
    )
    return {
        "task_uuid": str(task.task_uuid),
        "source_snapshot_id": source_snapshot.id,
        "source_snapshot_status": source_snapshot.status,
        "status": final_status,
        **task_result,
    }


def _refresh_stale_directories_for_snapshot(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    organization_id: int,
) -> None:
    bt = _bt()
    try:
        execution_target = bt._resolve_execution_target(source_snapshot=source_snapshot)
    except Exception:
        return
    total_dirs = max(int(source_snapshot.directory_count or 0), 1)
    node_online = effective_agent_node_status(execution_target.node) == Node.Availability.ONLINE
    for stale_index, directory_row in enumerate(
        BackupSourceSnapshotDirectory.objects.filter(source_snapshot=source_snapshot), start=1
    ):
        if directory_row.status != BackupSourceSnapshotDirectory.Status.FAILED:
            continue
        node_task = _get_node_task_for_directory(
            directory=directory_row,
            organization_id=organization_id,
            task_uuid=str(task.task_uuid),
            node_id=execution_target.node.id,
        )
        if node_task is None:
            continue
        _maybe_refresh_stale_directory_failure(
            task=task,
            source_snapshot=source_snapshot,
            directory_row=directory_row,
            node_task=node_task,
            directory_index=stale_index,
            total_dirs=total_dirs,
            node_online=node_online,
        )
    refresh_source_snapshot_summary(source_snapshot=source_snapshot)
    _backfill_terminal_backup_error_messages(task=task, source_snapshot=source_snapshot)


@transaction.atomic
def advance_backup(
    *,
    organization_id: int,
    task_uuid: str,
    source_snapshot_id: int | None = None,
) -> dict[str, Any]:
    bt = _bt()
    task = (
        Task.objects.select_for_update()
        .filter(organization_id=organization_id, task_uuid=task_uuid)
        .first()
    )
    if task is None:
        raise Task.DoesNotExist
    snapshot_qs = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        task_id=task.id,
    )
    if source_snapshot_id is not None:
        snapshot_qs = snapshot_qs.filter(pk=source_snapshot_id)
    source_snapshot = snapshot_qs.exclude(
        status__in=[
            BackupSourceSnapshot.Status.DELETING,
            BackupSourceSnapshot.Status.DELETE_FAILED,
            BackupSourceSnapshot.Status.DELETED,
        ]
    ).first()
    if source_snapshot is not None:
        _refresh_stale_directories_for_snapshot(
            task=task,
            source_snapshot=source_snapshot,
            organization_id=organization_id,
        )
        source_snapshot.refresh_from_db()
    if task.status in _TASK_TERMINAL:
        result = task.result_payload if isinstance(task.result_payload, dict) else {}
        snapshot_id = source_snapshot_id or (source_snapshot.id if source_snapshot else None) or result.get("source_snapshot_id")
        return {
            "task_uuid": str(task.task_uuid),
            "source_snapshot_id": snapshot_id,
            "source_snapshot_status": source_snapshot.status if source_snapshot else result.get("source_snapshot_status"),
            "status": task.status,
            **result,
        }
    if task.status == Task.Status.PENDING:
        task = start_task(task_uuid=task.task_uuid, organization_id=organization_id)

    snapshot_qs = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        task_id=task.id,
    )
    if source_snapshot_id is not None:
        snapshot_qs = snapshot_qs.filter(id=source_snapshot_id)
    source_snapshot = snapshot_qs.prefetch_related("directories").first()
    if source_snapshot is None:
        raise BackupSourceSnapshot.DoesNotExist

    set_source_snapshot_started(source_snapshot=source_snapshot, started_at=task.started_at or timezone.now())

    logic_step = bt._step(task, "create_logic_snapshot")
    if logic_step is None or logic_step.status != TaskStep.Status.SUCCESS:
        bt._set_step_status(
            task=task,
            step_name="create_logic_snapshot",
            status=TaskStep.Status.RUNNING,
            progress=10,
            task_progress=BACKUP_LOGIC_END / 2,
            current_step="create_logic_snapshot",
        )
        append_task_step_event(
            task=task,
            step_name="create_logic_snapshot",
            message="Logical snapshot created",
            metadata={
                "source_snapshot_id": source_snapshot.id,
                "object_name": source_snapshot.snapshot_uid,
            },
        )
        bt._set_step_status(
            task=task,
            step_name="create_logic_snapshot",
            status=TaskStep.Status.SUCCESS,
            progress=100,
            task_progress=BACKUP_LOGIC_END,
        )

    kopia_step = bt._step(task, "kopia_snapshot")
    if kopia_step is None or kopia_step.status not in {
        TaskStep.Status.RUNNING,
        TaskStep.Status.SUCCESS,
    }:
        bt._set_step_status(
            task=task,
            step_name="kopia_snapshot",
            status=TaskStep.Status.RUNNING,
            progress=0,
            task_progress=BACKUP_PREPARE_END,
            current_step="kopia_snapshot",
        )

    if kopia_step is not None and kopia_step.status == TaskStep.Status.SUCCESS:
        finalize_step = bt._step(task, "finalize_snapshot")
        if finalize_step is not None and finalize_step.status in {
            TaskStep.Status.SUCCESS,
            TaskStep.Status.FAILED,
        }:
            return {
                "task_uuid": str(task.task_uuid),
                "source_snapshot_id": source_snapshot.id,
                "status": task.status,
            }

    try:
        config = get_backup_config_for_snapshot(source_snapshot=source_snapshot)
        directories = backup_config_directories(
            backup_config_id=config.id,
            organization_id=organization_id,
        )
        if not directories:
            raise ValidationError({"directories": "Backup config has no directories."})
        execution_target = bt._resolve_execution_target(source_snapshot=source_snapshot)
        from apps.protection.services.repository_compatibility import (
            validate_backup_repository_compatible,
        )

        repository = validate_backup_repository_compatible(
            organization_id=organization_id,
            source_type=source_snapshot.source_type,
            source_ref_id=source_snapshot.source_ref_id,
            repository_id=config.repository_id,
        )
        runtime_policy = backup_runtime_policy_payload(source_snapshot.policy_snapshot)
    except Exception as exc:
        error_code = "BACKUP_PRECHECK_FAILED"
        error_message = str(exc)
        mark_source_snapshot_failed(
            source_snapshot=source_snapshot,
            error_code=error_code,
            error_message=error_message,
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=organization_id,
            status=Task.Status.FAILED,
            progress=BACKUP_PREPARE_END,
            result_payload={"source_snapshot_id": source_snapshot.id},
            error_code=error_code,
            error_message=error_message,
        )
        return {
            "task_uuid": str(task.task_uuid),
            "source_snapshot_id": source_snapshot.id,
            "status": Task.Status.FAILED,
        }

    # Directory size estimates are pre-cached asynchronously on backup-config save.
    # Do not sync path.size here: it contends with Celery soft limits and can load
    # the source host at backup start. Progress degrades when du_total is still 0.

    try:
        repository_payload = _repository_payload_for_backup(
            task=task,
            source_snapshot=source_snapshot,
            repository=repository,
            execution_target=execution_target,
            repository_endpoint_type=config.repository_endpoint_type,
        )
    except Exception as exc:
        error_code = "BACKUP_REPOSITORY_ACCESS_UNAVAILABLE"
        error_message = str(exc)
        mark_source_snapshot_failed(
            source_snapshot=source_snapshot,
            error_code=error_code,
            error_message=error_message,
        )
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=organization_id,
            status=Task.Status.FAILED,
            progress=BACKUP_PREPARE_END,
            result_payload={"source_snapshot_id": source_snapshot.id},
            error_code=error_code,
            error_message=error_message,
        )
        return {
            "task_uuid": str(task.task_uuid),
            "source_snapshot_id": source_snapshot.id,
            "status": Task.Status.FAILED,
        }
    if repository_payload is None:
        bt._set_step_status(
            task=task,
            step_name="kopia_snapshot",
            status=TaskStep.Status.RUNNING,
            progress=0,
            task_progress=BACKUP_PREPARE_END,
            current_step="kopia_snapshot",
        )
        return {
            "task_uuid": str(task.task_uuid),
            "source_snapshot_id": source_snapshot.id,
            "status": Task.Status.RUNNING,
            "orchestrator": "repository_server_starting",
        }
    try:
        repository_probe_ready = _ensure_source_repository_probe(
            task=task,
            source_snapshot=source_snapshot,
            execution_target=execution_target,
            repository=repository,
            repository_payload=repository_payload,
        )
    except Exception as exc:
        error_code = "BACKUP_REPOSITORY_ACCESS_UNAVAILABLE"
        error_message = str(exc)
        mark_source_snapshot_failed(
            source_snapshot=source_snapshot,
            error_code=error_code,
            error_message=error_message,
        )
        _stop_repository_server_for_task(task=task)
        complete_task(
            task_uuid=task.task_uuid,
            organization_id=organization_id,
            status=Task.Status.FAILED,
            progress=BACKUP_PREPARE_END,
            result_payload={"source_snapshot_id": source_snapshot.id},
            error_code=error_code,
            error_message=error_message,
        )
        return {
            "task_uuid": str(task.task_uuid),
            "source_snapshot_id": source_snapshot.id,
            "status": Task.Status.FAILED,
        }
    if not repository_probe_ready:
        bt._set_step_status(
            task=task,
            step_name="kopia_snapshot",
            status=TaskStep.Status.RUNNING,
            progress=0,
            task_progress=BACKUP_PREPARE_END,
            current_step="kopia_snapshot",
        )
        return {
            "task_uuid": str(task.task_uuid),
            "source_snapshot_id": source_snapshot.id,
            "status": Task.Status.RUNNING,
            "orchestrator": "repository_probe_running",
        }
    file_filter_payload = runtime_policy["file_filter"]
    backup_policy_payload = runtime_policy["backup_policy"]
    compression_payload = runtime_policy["compression"]

    total_dirs = len(directories)
    node_online = effective_agent_node_status(execution_target.node) == Node.Availability.ONLINE
    backup_protocol = _backup_protocol(task=task, node=execution_target.node)
    if backup_protocol == _BACKUP_PROTOCOL_PREPARED:
        policies_ready = _prepare_directory_policies(
            task=task,
            source_snapshot=source_snapshot,
            directories=directories,
            execution_target=execution_target,
            repository=repository,
            repository_payload=repository_payload,
            file_filter_payload=file_filter_payload,
            backup_policy_payload=backup_policy_payload,
            compression_payload=compression_payload,
        )
        if not policies_ready:
            bt._set_step_status(
                task=task,
                step_name="kopia_snapshot",
                status=TaskStep.Status.RUNNING,
                progress=0,
                task_progress=BACKUP_PREPARE_END,
                current_step="kopia_snapshot",
            )
            return {
                "task_uuid": str(task.task_uuid),
                "source_snapshot_id": source_snapshot.id,
                "status": Task.Status.RUNNING,
                "orchestrator": "policy_prepare",
                "backup_protocol": backup_protocol,
            }
    serial = (
        backup_protocol == _BACKUP_PROTOCOL_LEGACY
        and protection_conf.PROTECTION_BACKUP_DIRECTORY_CONCURRENCY.strip().lower() == "serial"
    )
    prior_failed = False

    prepared: list[tuple[int, Any, BackupSourceSnapshotDirectory, str, str]] = []
    policy_repair_pending = BackupSourceSnapshotDirectory.objects.filter(
        source_snapshot=source_snapshot,
        status=BackupSourceSnapshotDirectory.Status.PENDING,
        error_code="KOPIA_POLICY_REPAIR_PENDING",
    ).exists()

    for index, directory in enumerate(directories, start=1):
        source_path = bt._clean_path(directory.path)
        path_type = str(getattr(directory, "path_type", "") or "unknown")
        agent_source_path = source_path
        if execution_target.root_path:
            from apps.source.services.internal.nas_share_path import to_mount_path

            agent_source_path = to_mount_path(execution_target.root_path, source_path)

        directory_row = BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=source_snapshot,
            backup_config_dir_id=directory.id,
        ).first()

        if directory_row is not None and directory_row.status in _DIRECTORY_TERMINAL:
            continue

        if serial and prior_failed:
            record_source_snapshot_directory_result(
                source_snapshot=source_snapshot,
                backup_config_dir_id=directory.id,
                source_path=source_path,
                path_type=path_type,
                display_name=directory.display_name,
                repository_id=repository.id,
                status=BackupSourceSnapshotDirectory.Status.FAILED,
                error_code="PRIOR_DIRECTORY_FAILED",
                error_message="Skipped because a prior directory failed in serial mode.",
            )
            prior_failed = True
            continue

        if execution_target.root_path and not bt._is_subpath(execution_target.root_path, agent_source_path):
            record_source_snapshot_directory_result(
                source_snapshot=source_snapshot,
                backup_config_dir_id=directory.id,
                source_path=source_path,
                path_type=path_type,
                display_name=directory.display_name,
                repository_id=repository.id,
                status=BackupSourceSnapshotDirectory.Status.FAILED,
                error_code="SOURCE_PATH_FORBIDDEN",
                error_message="Backup config source path is outside the mounted NAS path.",
            )
            prior_failed = True
            continue

        if directory_row is None:
            directory_row = record_source_snapshot_directory_result(
                source_snapshot=source_snapshot,
                backup_config_dir_id=directory.id,
                source_path=source_path,
                path_type=path_type,
                display_name=directory.display_name,
                repository_id=repository.id,
                status=BackupSourceSnapshotDirectory.Status.PENDING,
            )
            append_task_step_event(
                task=task,
                step_name="kopia_snapshot",
                message="Starting directory snapshot",
                metadata={
                    "backup_config_dir_id": directory.id,
                    "source_path": source_path,
                    "object_name": source_path,
                },
            )

        if directory_row.status in {
            BackupSourceSnapshotDirectory.Status.PENDING,
            BackupSourceSnapshotDirectory.Status.CREATING,
        }:
            dispatch_kind = (
                "backup.snapshot.create"
                if backup_protocol == _BACKUP_PROTOCOL_PREPARED
                else "backup.run"
            )
            if policy_repair_pending:
                if directory_row.error_code != "KOPIA_POLICY_REPAIR_PENDING":
                    continue
                sibling_active = BackupSourceSnapshotDirectory.objects.filter(
                    source_snapshot=source_snapshot,
                    status__in={
                        BackupSourceSnapshotDirectory.Status.DISPATCHING,
                        BackupSourceSnapshotDirectory.Status.RUNNING,
                        BackupSourceSnapshotDirectory.Status.CREATING,
                    },
                ).exclude(pk=directory_row.pk).exists()
                if sibling_active:
                    continue
                dispatch_kind = "backup.run"
                directory_row.error_code = ""
                directory_row.error_message = ""
                directory_row.last_substantive_progress_at = None
                directory_row.last_progress_snapshot = {}
                directory_row.last_progress_sample = {}
                directory_row.cancel_requested_at = None
                directory_row.stall_warned_at = None
                directory_row.save(
                    update_fields=[
                        "error_code",
                        "error_message",
                        "last_substantive_progress_at",
                        "last_progress_snapshot",
                        "last_progress_sample",
                        "cancel_requested_at",
                        "stall_warned_at",
                        "updated_at",
                    ]
                )
            _dispatch_directory_backup(
                task=task,
                source_snapshot=source_snapshot,
                directory_row=directory_row,
                config_directory=directory,
                execution_target=execution_target,
                repository=repository,
                repository_payload=repository_payload,
                file_filter_payload=file_filter_payload,
                backup_policy_payload=backup_policy_payload,
                compression_payload=compression_payload,
                agent_source_path=agent_source_path,
                source_path=source_path,
                task_kind=dispatch_kind,
            )
            directory_row.refresh_from_db()

        if directory_row.status not in _DIRECTORY_TERMINAL:
            prepared.append((index, directory, directory_row, source_path, agent_source_path))
        if serial and _directory_in_progress(directory_row.status):
            break

    for stale_index, directory_row in enumerate(
        BackupSourceSnapshotDirectory.objects.filter(source_snapshot=source_snapshot), start=1
    ):
        if directory_row.status != BackupSourceSnapshotDirectory.Status.FAILED:
            continue
        if str(directory_row.error_code or "") not in _STALE_DIRECTORY_FAILURE_CODES:
            continue
        node_task = _get_node_task_for_directory(
            directory=directory_row,
            organization_id=organization_id,
            task_uuid=str(task.task_uuid),
            node_id=execution_target.node.id,
        )
        if node_task is None:
            continue
        _maybe_refresh_stale_directory_failure(
            task=task,
            source_snapshot=source_snapshot,
            directory_row=directory_row,
            node_task=node_task,
            directory_index=stale_index,
            total_dirs=total_dirs,
            node_online=node_online,
        )

    for index, directory, directory_row, source_path, agent_source_path in prepared:
        directory_row.refresh_from_db()
        if directory_row.status in _DIRECTORY_TERMINAL:
            if directory_row.status == BackupSourceSnapshotDirectory.Status.FAILED:
                prior_failed = True
            continue

        node_task = _get_node_task_for_directory(
            directory=directory_row,
            organization_id=organization_id,
            task_uuid=str(task.task_uuid),
            node_id=execution_target.node.id,
        )
        if node_task is None:
            directory_row.status = BackupSourceSnapshotDirectory.Status.PENDING
            directory_row.node_task_id = None
            directory_row.save(update_fields=["status", "node_task_id", "updated_at"])
            continue

        _observe_running_directory(
            task=task,
            source_snapshot=source_snapshot,
            directory_row=directory_row,
            node_task=node_task,
            directory_index=index,
            total_dirs=total_dirs,
            node_online=node_online,
            execution_node=execution_target.node,
        )
        directory_row.refresh_from_db()
        if directory_row.status == BackupSourceSnapshotDirectory.Status.FAILED:
            prior_failed = True
        if serial and _directory_in_progress(directory_row.status):
            break

    rows = list(
        BackupSourceSnapshotDirectory.objects.filter(source_snapshot=source_snapshot)
    )
    if not rows or any(_directory_in_progress(row.status) for row in rows):
        successful = sum(
            1 for row in rows if row.status == BackupSourceSnapshotDirectory.Status.AVAILABLE
        )
        if rows:
            step_progress, task_progress = bt._backup_success_progress(successful, total_dirs)
            bt._set_step_status(
                task=task,
                step_name="kopia_snapshot",
                status=TaskStep.Status.RUNNING,
                progress=step_progress,
                task_progress=task_progress,
            )
        return {
            "task_uuid": str(task.task_uuid),
            "source_snapshot_id": source_snapshot.id,
            "status": Task.Status.RUNNING,
            "orchestrator": "in_progress",
        }

    return _finalize_backup_task(
        task=task,
        source_snapshot=source_snapshot,
        organization_id=organization_id,
        total_dirs=total_dirs,
    )


def reconcile_backup_tasks(*, limit: int = 100) -> dict[str, int]:
    """Advance all in-flight backup tasks (replaces blocking reconcile queue loop)."""
    tasks = list(
        Task.objects.filter(
            task_type=Task.Type.BACKUP,
            status__in={Task.Status.PENDING, Task.Status.RUNNING},
        )
        .order_by("updated_at", "id")[: max(1, int(limit))]
    )
    advanced = 0
    queue_requeued = 0
    queue_timed_out = 0
    for task in tasks:
        snapshot = BackupSourceSnapshot.objects.filter(
            organization_id=task.organization_id,
            task_id=task.id,
        ).exclude(
            status__in=[
                BackupSourceSnapshot.Status.DELETING,
                BackupSourceSnapshot.Status.DELETE_FAILED,
                BackupSourceSnapshot.Status.DELETED,
            ]
        ).first()
        if snapshot is None:
            continue
        queue_action = _reconcile_pending_backup_queue(task=task, source_snapshot=snapshot)
        if queue_action in {
            "requeued",
            "already_requeued",
            "recovery_grace",
            "recovery_hold",
        }:
            if queue_action == "requeued":
                queue_requeued += 1
            continue
        if queue_action == "timed_out":
            queue_timed_out += 1
            continue
        try:
            advance_backup(
                organization_id=task.organization_id,
                task_uuid=str(task.task_uuid),
                source_snapshot_id=snapshot.id,
            )
            advanced += 1
        except Exception:
            logger.exception(
                "backup reconcile advance failed task_uuid=%s snapshot_id=%s",
                task.task_uuid,
                snapshot.id,
            )
    projected = reconcile_backup_result_projections(limit=limit)
    return {
        "candidates": len(tasks),
        "advanced": advanced,
        "queue_requeued": queue_requeued,
        "queue_timed_out": queue_timed_out,
        "projection_candidates": projected["candidates"],
        "projected": projected["projected"],
    }


_QUEUE_RECOVERY_KEY = "backup_queue_recovery"


def _queue_recovery_timestamp(task: Task):
    state = _task_result_payload(task).get(_QUEUE_RECOVERY_KEY)
    if not isinstance(state, dict):
        return None
    raw = str(state.get("attempted_at") or "").strip()
    parsed = parse_datetime(raw) if raw else None
    if parsed is not None and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _requeue_same_backup_task(*, task: Task, source_snapshot: BackupSourceSnapshot) -> None:
    from apps.protection.tasks.backup import execute_backup_source_task

    execute_backup_source_task.delay(
        organization_id=task.organization_id,
        task_uuid=str(task.task_uuid),
        source_snapshot_id=source_snapshot.id,
    )


def _finalize_pending_backup_queue_timeout(
    *, task: Task, source_snapshot: BackupSourceSnapshot
) -> bool:
    """Seal a never-started backup and release its active snapshot constraint."""
    with transaction.atomic():
        locked = (
            Task.objects.select_for_update()
            .filter(
                pk=task.pk,
                status=Task.Status.PENDING,
                started_at__isnull=True,
            )
            .first()
        )
        if locked is None:
            return False
        snapshot = (
            BackupSourceSnapshot.objects.select_for_update()
            .filter(pk=source_snapshot.pk, task_id=locked.id)
            .first()
        )
        if snapshot is None:
            return False
        now = timezone.now()
        message = "Backup remained queued after one recovery attempt."
        BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot_id=snapshot.id,
            status__in=_DIRECTORY_IN_PROGRESS,
        ).update(
            status=BackupSourceSnapshotDirectory.Status.FAILED,
            error_code="TASK_QUEUE_TIMEOUT",
            error_message=message,
            updated_at=now,
        )
        mark_source_snapshot_failed(
            source_snapshot=snapshot,
            error_code="TASK_QUEUE_TIMEOUT",
            error_message=message,
            finished_at=now,
        )
        TaskStep.objects.filter(
            task=locked,
            status__in=[TaskStep.Status.PENDING, TaskStep.Status.RUNNING],
        ).update(status=TaskStep.Status.FAILED)
        payload = _task_result_payload(locked)
        payload.update(
            {
                "source_snapshot_id": snapshot.id,
                "source_snapshot_status": BackupSourceSnapshot.Status.FAILED,
                "queue_timeout_sealed": True,
            }
        )
        complete_task(
            task_uuid=locked.task_uuid,
            organization_id=locked.organization_id,
            status=Task.Status.TIMEOUT,
            progress=float(locked.progress),
            result_payload=payload,
            error_code="TASK_QUEUE_TIMEOUT",
            error_message=message,
        )
    _stop_repository_server_for_task(task=locked)
    return True


def _reconcile_pending_backup_queue(
    *, task: Task, source_snapshot: BackupSourceSnapshot
) -> str:
    """Conservative two-stage recovery for platform tasks never claimed by a worker."""
    if task.status != Task.Status.PENDING or task.started_at is not None:
        return "not_pending"
    from apps.node.services.internal import redis_store

    if redis_store.ws_recovery_hold_active():
        return "recovery_hold"
    now = timezone.now()
    stale_after = timezone.timedelta(
        seconds=max(1, protection_conf.PROTECTION_BACKUP_QUEUE_STALE_SECONDS)
    )
    if now - task.created_at < stale_after:
        return "waiting"
    attempted_at = _queue_recovery_timestamp(task)
    if attempted_at is None:
        with transaction.atomic():
            locked = (
                Task.objects.select_for_update()
                .filter(pk=task.pk, status=Task.Status.PENDING, started_at__isnull=True)
                .first()
            )
            if locked is None:
                return "claimed"
            if _queue_recovery_timestamp(locked) is not None:
                return "already_requeued"
            payload = _task_result_payload(locked)
            payload[_QUEUE_RECOVERY_KEY] = {
                "attempted_at": now.isoformat(),
                "attempt_count": 1,
            }
            _save_task_result_payload(locked, payload)
            append_task_step_event(
                task=locked,
                step_name=locked.current_step or "queued",
                level=TaskEvent.Level.WARN,
                message="Backup queue recovery re-enqueued the original task",
                metadata={
                    "source_snapshot_id": source_snapshot.id,
                    "attempt_count": 1,
                    "object_name": source_snapshot.snapshot_uid,
                },
            )
        try:
            _requeue_same_backup_task(task=locked, source_snapshot=source_snapshot)
        except Exception:
            logger.exception(
                "backup queue recovery enqueue failed task_uuid=%s snapshot_id=%s",
                locked.task_uuid,
                source_snapshot.id,
            )
        return "requeued"
    grace = timezone.timedelta(
        seconds=max(1, protection_conf.PROTECTION_BACKUP_QUEUE_RECOVERY_GRACE_SECONDS)
    )
    if now - attempted_at < grace:
        return "recovery_grace"
    return (
        "timed_out"
        if _finalize_pending_backup_queue_timeout(task=task, source_snapshot=source_snapshot)
        else "claimed"
    )


_LATE_RESULT_DIRECTORY_ACTIVE = frozenset(
    {
        BackupSourceSnapshotDirectory.Status.PENDING,
        BackupSourceSnapshotDirectory.Status.DISPATCHING,
        BackupSourceSnapshotDirectory.Status.RUNNING,
        BackupSourceSnapshotDirectory.Status.CREATING,
    }
)
_SNAPSHOT_DELETE_INTENT = frozenset(
    {
        BackupSourceSnapshot.Status.DELETING,
        BackupSourceSnapshot.Status.DELETE_FAILED,
        BackupSourceSnapshot.Status.DELETED,
    }
)


def _late_directory_failure_is_transient(directory: BackupSourceSnapshotDirectory) -> bool:
    code = str(directory.error_code or "").strip().upper()
    if code in protection_conf.LATE_SUCCESS_ADOPT_ERROR_CODES:
        return True
    message = str(directory.error_message or "").strip().lower()
    return code == "AGENT_BACKUP_FAILED" and "agent went offline" in message


def _backup_projection_payload(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
) -> dict[str, Any]:
    payload = dict(task.result_payload) if isinstance(task.result_payload, dict) else {}
    payload.update(
        {
            "source_snapshot_id": source_snapshot.id,
            "source_snapshot_status": source_snapshot.status,
            "successful_directory_count": source_snapshot.successful_directory_count,
            "failed_directory_count": source_snapshot.failed_directory_count,
            "directory_count": source_snapshot.directory_count,
            "total_size_bytes": source_snapshot.total_size_bytes,
            "file_count": source_snapshot.file_count,
            "dir_count": source_snapshot.dir_count,
        }
    )
    return payload


def _correct_terminal_backup_task_success(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    node_task: NodeTask,
) -> None:
    previous_status = task.status
    payload = _backup_projection_payload(task=task, source_snapshot=source_snapshot)
    task.status = Task.Status.SUCCESS
    task.progress = 100
    task.result_payload = payload
    task.error_code = None
    task.error_message = None
    task.current_step = "finalize_snapshot"
    task.finished_at = task.finished_at or timezone.now()
    task.save(
        update_fields=[
            "status",
            "progress",
            "result_payload",
            "error_code",
            "error_message",
            "current_step",
            "finished_at",
            "updated_at",
        ]
    )
    TaskStep.objects.filter(
        task=task,
        step_name__in=["kopia_snapshot", "finalize_snapshot"],
    ).update(status=TaskStep.Status.SUCCESS, progress=100)
    append_task_step_event(
        task=task,
        step_name="finalize_snapshot",
        message="Backup terminal state corrected after durable late Agent result",
        metadata={
            "previous_status": previous_status,
            "source_snapshot_id": source_snapshot.id,
            "node_task_id": str(node_task.id),
            "object_name": source_snapshot.snapshot_uid,
        },
    )
    task_updated.send(
        sender=Task,
        task_uuid=str(task.task_uuid),
        organization_id=task.organization_id,
        status=task.status,
        progress=float(task.progress),
    )


@transaction.atomic
def project_backup_node_task_result(*, node_task_id) -> dict[str, Any]:
    """Idempotently project one durable backup NodeTask into snapshot/task state."""
    node_task = NodeTask.objects.select_for_update().filter(pk=node_task_id).first()
    if (
        node_task is None
        or node_task.correlation_type != protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE
        or not node_task.correlation_id
    ):
        return {"status": "ignored", "reason": "not_backup_result"}

    task = (
        Task.objects.select_for_update()
        .filter(
            organization_id=node_task.organization_id,
            task_type=Task.Type.BACKUP,
            task_uuid=node_task.correlation_id,
        )
        .first()
    )
    if task is None:
        return {"status": "ignored", "reason": "task_missing"}
    source_snapshot = (
        BackupSourceSnapshot.objects.select_for_update()
        .filter(organization_id=node_task.organization_id, task_id=task.id)
        .first()
    )
    if source_snapshot is None:
        return {"status": "ignored", "reason": "snapshot_missing"}
    if source_snapshot.status in _SNAPSHOT_DELETE_INTENT:
        return {"status": "ignored", "reason": "snapshot_delete_intent"}

    if task.status not in _TASK_TERMINAL:
        result = advance_backup(
            organization_id=task.organization_id,
            task_uuid=str(task.task_uuid),
            source_snapshot_id=source_snapshot.id,
        )
        return {"status": "advanced", "result": result}

    if task.status not in {Task.Status.FAILED, Task.Status.TIMEOUT}:
        return {"status": "ignored", "reason": "terminal_task_not_recoverable"}
    if node_task.status != NodeTask.Status.SUCCESS:
        return {"status": "ignored", "reason": "node_task_not_success"}

    node_payload = node_task.payload if isinstance(node_task.payload, dict) else {}
    directory_id = int(node_payload.get("backup_config_dir_id") or 0)
    directory = BackupSourceSnapshotDirectory.objects.select_for_update().filter(
        source_snapshot=source_snapshot,
        node_task_id=node_task.id,
    ).first()
    if directory is None and directory_id > 0:
        directory = BackupSourceSnapshotDirectory.objects.select_for_update().filter(
            source_snapshot=source_snapshot,
            backup_config_dir_id=directory_id,
            node_task_id__isnull=True,
        ).first()
    if directory is None:
        return {"status": "ignored", "reason": "directory_missing_or_superseded"}
    if directory.status == BackupSourceSnapshotDirectory.Status.AVAILABLE:
        return {"status": "unchanged", "reason": "already_projected"}
    if directory.status == BackupSourceSnapshotDirectory.Status.DELETED:
        return {"status": "ignored", "reason": "directory_deleted"}
    if (
        directory.status == BackupSourceSnapshotDirectory.Status.FAILED
        and not _late_directory_failure_is_transient(directory)
    ):
        return {"status": "ignored", "reason": "non_transient_directory_failure"}
    if directory.status not in _LATE_RESULT_DIRECTORY_ACTIVE | {
        BackupSourceSnapshotDirectory.Status.FAILED
    }:
        return {"status": "ignored", "reason": "directory_not_recoverable"}

    bt = _bt()
    snapshot_id, size_bytes, file_count, dir_count, stats = bt._extract_snapshot_metrics(
        node_task.result if isinstance(node_task.result, dict) else {}
    )
    snapshot_id = normalize_kopia_snapshot_id(snapshot_id)
    if not snapshot_id:
        return {"status": "ignored", "reason": "kopia_snapshot_id_missing"}

    previous_error_code = directory.error_code
    repository = Repository.objects.filter(
        organization_id=directory.organization_id,
        id=directory.repository_id,
    ).first()
    if repository is not None:
        ensure_snapshot_repository_locator(
            directory=directory,
            repository=repository,
            writer_node_id=node_task.node_id,
        )
    projected_directory = record_source_snapshot_directory_result(
        source_snapshot=source_snapshot,
        backup_config_dir_id=directory.backup_config_dir_id,
        source_path=directory.source_path,
        path_type=directory.path_type,
        display_name=directory.display_name,
        repository_id=directory.repository_id,
        status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        kopia_snapshot_id=snapshot_id,
        size_bytes=size_bytes,
        file_count=file_count,
        dir_count=dir_count,
        stats=stats,
    )
    projected_directory.node_task_id = node_task.id
    projected_directory.adopted_late_result = True
    projected_directory.save(
        update_fields=["node_task_id", "adopted_late_result", "updated_at"]
    )
    append_task_step_event(
        task=task,
        step_name="kopia_snapshot",
        message="Late backup result adopted",
        metadata={
            "backup_config_dir_id": projected_directory.backup_config_dir_id,
            "node_task_id": str(node_task.id),
            "kopia_snapshot_id": snapshot_id,
            "previous_error_code": previous_error_code,
            "source_path": projected_directory.source_path,
            "object_id": snapshot_id,
            "object_name": projected_directory.source_path,
        },
    )

    source_snapshot.refresh_from_db()
    if (
        source_snapshot.status == BackupSourceSnapshot.Status.AVAILABLE
        and source_snapshot.successful_directory_count == source_snapshot.directory_count
    ):
        _correct_terminal_backup_task_success(
            task=task,
            source_snapshot=source_snapshot,
            node_task=node_task,
        )
        corrected = True
    else:
        task.result_payload = _backup_projection_payload(
            task=task,
            source_snapshot=source_snapshot,
        )
        task.save(update_fields=["result_payload", "updated_at"])
        corrected = False
    return {
        "status": "projected",
        "source_snapshot_id": source_snapshot.id,
        "source_snapshot_status": source_snapshot.status,
        "task_corrected": corrected,
    }


def queue_backup_result_projection(*, node_task: NodeTask) -> bool:
    if (
        node_task.correlation_type
        == protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE
    ):
        if not node_task.correlation_id:
            return False
        snapshot = BackupSourceSnapshot.objects.filter(
            organization_id=node_task.organization_id,
            task_uuid=node_task.correlation_id,
        ).only("id").first()
        if snapshot is None:
            return False
        from apps.protection.tasks.backup import advance_backup_task

        advance_backup_task.delay(
            organization_id=node_task.organization_id,
            task_uuid=str(node_task.correlation_id),
            source_snapshot_id=snapshot.id,
        )
        return True
    if node_task.correlation_type != protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE:
        return False
    from apps.protection.tasks.backup import project_backup_node_task_result_task

    project_backup_node_task_result_task.delay(node_task_id=str(node_task.id))
    return True


def reconcile_backup_result_projections(*, limit: int = 100) -> dict[str, int]:
    rows = list(
        BackupSourceSnapshotDirectory.objects.exclude(
            source_snapshot__status__in=_SNAPSHOT_DELETE_INTENT,
        )
        .filter(
            Q(status__in=_LATE_RESULT_DIRECTORY_ACTIVE)
            | Q(
                status=BackupSourceSnapshotDirectory.Status.FAILED,
                error_code__in=protection_conf.LATE_SUCCESS_ADOPT_ERROR_CODES,
            )
            | Q(
                status=BackupSourceSnapshotDirectory.Status.FAILED,
                error_code="AGENT_BACKUP_FAILED",
                error_message__icontains="Agent went offline",
            )
        )
        .select_related("source_snapshot")
        .order_by("-updated_at", "-id")[: max(1, int(limit))]
    )
    projected = 0
    for directory in rows:
        node_task = None
        if directory.node_task_id:
            node_task = NodeTask.objects.filter(
                pk=directory.node_task_id,
                status=NodeTask.Status.SUCCESS,
            ).first()
        if node_task is None:
            node_task = (
                NodeTask.objects.filter(
                    organization_id=directory.organization_id,
                    correlation_type=protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
                    correlation_id=str(directory.source_snapshot.task_uuid),
                    payload__backup_config_dir_id=directory.backup_config_dir_id,
                    status=NodeTask.Status.SUCCESS,
                )
                .order_by("-updated_at", "-created_at")
                .first()
            )
        if node_task is None:
            continue
        try:
            outcome = project_backup_node_task_result(node_task_id=node_task.id)
        except Exception:
            logger.exception(
                "backup result projection reconcile failed node_task_id=%s directory_id=%s",
                node_task.id,
                directory.id,
            )
            continue
        if outcome.get("status") == "projected":
            projected += 1
    return {"candidates": len(rows), "projected": projected}


def _finalize_cancelled_backup_snapshot(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    reason: str = "",
) -> BackupSourceSnapshot:
    now = timezone.now()
    message = str(reason or task.error_message or "Task cancelled by user").strip()
    config_directories = list(
        backup_config_directories(
            backup_config_id=source_snapshot.backup_config_id,
            organization_id=source_snapshot.organization_id,
        )
    )
    directories_by_config_id = {
        row.backup_config_dir_id: row
        for row in BackupSourceSnapshotDirectory.objects.filter(source_snapshot=source_snapshot)
    }
    policy_tasks = NodeTask.objects.filter(
        organization_id=task.organization_id,
        correlation_type=protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE,
        correlation_id=str(task.task_uuid),
        status__in={NodeTask.Status.PENDING, NodeTask.Status.RUNNING},
    )
    for policy_task in policy_tasks:
        try:
            cancel_agent_task(task_id=policy_task.id, reason="user canceled backup")
        except Exception:
            logger.exception(
                "failed to dispatch backup policy cancel node_task_id=%s task_uuid=%s",
                policy_task.id,
                task.task_uuid,
            )
    for config_directory in config_directories:
        if config_directory.id in directories_by_config_id:
            continue
        directories_by_config_id[config_directory.id] = BackupSourceSnapshotDirectory.objects.create(
            source_snapshot=source_snapshot,
            organization_id=source_snapshot.organization_id,
            backup_config_id=source_snapshot.backup_config_id,
            backup_config_dir_id=config_directory.id,
            source_path=config_directory.path,
            path_type=getattr(config_directory, "path_type", BackupSourceSnapshotDirectory.PathType.UNKNOWN),
            display_name=config_directory.display_name,
            repository_id=source_snapshot.repository_id,
            status=BackupSourceSnapshotDirectory.Status.PENDING,
        )

    for directory_row in directories_by_config_id.values():
        if directory_row.status in _DIRECTORY_TERMINAL:
            continue
        if directory_row.node_task_id:
            try:
                cancel_agent_task(task_id=directory_row.node_task_id, reason="user canceled backup")
            except Exception:
                logger.exception(
                    "failed to dispatch backup directory cancel node_task_id=%s task_uuid=%s",
                    directory_row.node_task_id,
                    task.task_uuid,
                )
        directory_row.status = BackupSourceSnapshotDirectory.Status.CANCELLED
        directory_row.error_code = "TASK_CANCELLED"
        directory_row.error_message = message
        directory_row.cancel_requested_at = now
        directory_row.save(
            update_fields=[
                "status",
                "error_code",
                "error_message",
                "cancel_requested_at",
                "updated_at",
            ]
        )
        append_task_step_event(
            task=task,
            step_name="kopia_snapshot",
            level=TaskEvent.Level.WARN,
            message="Directory backup cancelled",
            metadata={
                "backup_config_dir_id": directory_row.backup_config_dir_id,
                "source_path": directory_row.source_path,
                "node_task_id": str(directory_row.node_task_id or ""),
                "error_code": "TASK_CANCELLED",
                "error_message": message,
                "object_name": directory_row.source_path,
            },
        )

    source_snapshot.directory_count = max(
        int(source_snapshot.directory_count or 0),
        len(config_directories),
        len(directories_by_config_id),
    )
    source_snapshot.save(update_fields=["directory_count", "updated_at"])
    source_snapshot = refresh_source_snapshot_summary(source_snapshot=source_snapshot, finished_at=now)
    if source_snapshot.status in {BackupSourceSnapshot.Status.FAILED, BackupSourceSnapshot.Status.PARTIAL}:
        updates: list[str] = []
        if not str(source_snapshot.error_code or "").strip():
            source_snapshot.error_code = "TASK_CANCELLED"
            updates.append("error_code")
        if not str(source_snapshot.error_message or "").strip():
            source_snapshot.error_message = message
            updates.append("error_message")
        if updates:
            updates.append("updated_at")
            source_snapshot.save(update_fields=updates)
    return source_snapshot


def cancel_backup(*, organization_id: int, task_uuid: str) -> dict[str, Any]:
    task = Task.objects.filter(
        organization_id=organization_id,
        task_uuid=task_uuid,
        task_type=Task.Type.BACKUP,
    ).first()
    if task is None:
        raise Task.DoesNotExist
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        task_id=task.id,
    ).prefetch_related("directories").first()
    if snapshot is None and task.status in _TASK_TERMINAL:
        return {"task_uuid": str(task.task_uuid), "status": task.status}
    if snapshot is None:
        raise BackupSourceSnapshot.DoesNotExist
    if task.status in _TASK_TERMINAL:
        if task.status == Task.Status.CANCELLED and snapshot.status == BackupSourceSnapshot.Status.CREATING:
            snapshot = _finalize_cancelled_backup_snapshot(
                task=task,
                source_snapshot=snapshot,
                reason=task.error_message,
            )
            _stop_repository_server_for_task(task=task)
        return {
            "task_uuid": str(task.task_uuid),
            "status": task.status,
            "source_snapshot_id": snapshot.id,
            "source_snapshot_status": snapshot.status,
        }
    from apps.task.services.interface import cancel_task as cancel_platform_task

    task = cancel_platform_task(task_uuid=task.task_uuid, organization_id=organization_id)
    snapshot = _finalize_cancelled_backup_snapshot(
        task=task,
        source_snapshot=snapshot,
        reason=task.error_message,
    )
    _stop_repository_server_for_task(task=task)
    advance_backup(
        organization_id=organization_id,
        task_uuid=str(task.task_uuid),
        source_snapshot_id=snapshot.id,
    )
    task.refresh_from_db()
    snapshot.refresh_from_db()
    return {
        "task_uuid": str(task.task_uuid),
        "status": task.status,
        "source_snapshot_id": snapshot.id,
        "source_snapshot_status": snapshot.status,
    }


def retry_backup_directory(
    *,
    organization_id: int,
    task_uuid: str,
    backup_config_dir_id: int,
) -> dict[str, Any]:
    task = Task.objects.filter(
        organization_id=organization_id,
        task_uuid=task_uuid,
        task_type=Task.Type.BACKUP,
    ).first()
    if task is None:
        raise Task.DoesNotExist
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        task_id=task.id,
    ).first()
    if snapshot is None:
        raise BackupSourceSnapshot.DoesNotExist
    directory_row = BackupSourceSnapshotDirectory.objects.filter(
        source_snapshot=snapshot,
        backup_config_dir_id=int(backup_config_dir_id),
        status=BackupSourceSnapshotDirectory.Status.FAILED,
    ).first()
    if directory_row is None:
        raise ValidationError({"backup_config_dir_id": "Failed directory not found for retry."})
    directory_row.status = BackupSourceSnapshotDirectory.Status.PENDING
    directory_row.node_task_id = None
    directory_row.retry_count = int(directory_row.retry_count or 0) + 1
    directory_row.error_code = ""
    directory_row.error_message = ""
    directory_row.cancel_requested_at = None
    directory_row.stall_warned_at = None
    directory_row.adopted_late_result = False
    directory_row.dispatched_at = None
    directory_row.last_substantive_progress_at = None
    directory_row.last_progress_snapshot = {}
    directory_row.last_progress_sample = {}
    directory_row.save()
    if task.status in _TASK_TERMINAL:
        from apps.task.services.interface import retry_task

        retry_task(task_uuid=task.task_uuid, organization_id=organization_id)
    bt = _bt()
    bt._set_step_status(
        task=task,
        step_name="kopia_snapshot",
        status=TaskStep.Status.RUNNING,
        progress=0,
        task_progress=BACKUP_PREPARE_END,
        current_step="kopia_snapshot",
    )
    return advance_backup(
        organization_id=organization_id,
        task_uuid=str(task.task_uuid),
        source_snapshot_id=snapshot.id,
    )


def maybe_trigger_backup_advance(
    *,
    node_task: NodeTask,
) -> None:
    """Called from WS handlers after backup NodeTask progress/result."""
    if node_task.correlation_type not in {
        protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE,
        protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE,
    }:
        return
    if not node_task.correlation_id:
        return
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=node_task.organization_id,
        task_uuid=node_task.correlation_id,
    ).first()
    if snapshot is None:
        return
    try:
        if node_task.status in _NODE_TASK_TERMINAL:
            advance_backup(
                organization_id=node_task.organization_id,
                task_uuid=str(node_task.correlation_id),
                source_snapshot_id=snapshot.id,
            )
            return
        if (
            node_task.correlation_type
            == protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE
        ):
            return
        from apps.protection.services.progress.backup_runtime import (
            sync_backup_directory_progress_from_node_task,
        )

        sync_backup_directory_progress_from_node_task(node_task=node_task)
    except Exception:
        logger.exception(
            "backup advance trigger failed task_uuid=%s node_task_id=%s",
            node_task.correlation_id,
            node_task.id,
        )


def reattach_backup_node_task(
    *,
    node_task: NodeTask,
) -> NodeTask | None:
    """Restore a backup NodeTask falsely marked timeout within reattach grace."""
    if node_task.correlation_type != protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE:
        return None
    if node_task.status != NodeTask.Status.TIMEOUT:
        return None
    if node_task.kind not in protection_conf.PROTECTION_BACKUP_NODE_TASK_KINDS:
        return None
    grace = protection_conf.PROTECTION_BACKUP_REATTACH_GRACE_SECONDS
    if grace <= 0:
        return None
    reference = node_task.updated_at or node_task.last_progress_at
    if reference is None:
        return None
    if (timezone.now() - reference).total_seconds() > grace:
        return None
    node_task.status = NodeTask.Status.RUNNING
    node_task.last_error = ""
    node_task.watchdog_deadline_at = timezone.now() + timedelta(
        seconds=protection_conf.PROTECTION_BACKUP_ACTIVITY_LEASE_SECONDS,
    )
    node_task.save(
        update_fields=["status", "last_error", "watchdog_deadline_at", "updated_at"]
    )
    logger.info(
        "reattached backup node task task_id=%s correlation_id=%s",
        node_task.id,
        node_task.correlation_id,
    )
    return node_task
