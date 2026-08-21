from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.node.models import Node, NodeTask
from apps.node.services.interface import AgentTaskSyncResult, run_agent_task_async
from apps.node.services.internal.node_registry import node_is_available_for_work
from apps.node.models.base import NodeRole
from apps.protection.models import BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.protection.services.source_execution import resolve_source_execution_target
from apps.protection.services.snapshot_repository_locator import (
    resolve_snapshot_repository_locator,
    resolve_snapshot_repository_reader,
)
from apps.storage.repositories.models import Repository
from apps.storage.services.internal.kopia_cli import delete_s3_snapshots
from apps.storage.services.internal.repository_execution_lock import (
    repository_execution_lock,
)
from apps.storage.services.internal.repository_access import repository_uses_bound_proxy
from apps.task.models import Task


@dataclass(frozen=True)
class SnapshotDeleteOutcome:
    task: _ControllerTask
    result: dict[str, Any]
    ok: bool
    timed_out: bool = False


@dataclass(frozen=True)
class _ControllerTask:
    id: str = "controller-snapshot-delete"
    status: str = "success"
    last_error: str = ""


class ControllerSnapshotDeleteBusy(RuntimeError):
    """The single Controller-side Kopia deletion slot is in use."""


class AgentSnapshotDeletePending(RuntimeError):
    """A durable Agent snapshot deletion is still running."""


def snapshot_delete_agent_work_active(*, task: Task) -> bool:
    """Return whether an Agent snapshot deletion still owns the parent task."""

    return NodeTask.objects.filter(
        parent_task=task,
        kind="snapshot.delete",
        correlation_type__in={
            "protection.snapshot_delete",
            "protection.backup_config_reset",
        },
        status__in={NodeTask.Status.PENDING, NodeTask.Status.RUNNING},
    ).exists()


def queue_snapshot_delete_result_followup(*, node_task: NodeTask) -> bool:
    """Resume the owning product task after a durable Agent delete terminates."""

    if (
        node_task.kind != "snapshot.delete"
        or node_task.correlation_type
        not in {
            "protection.snapshot_delete",
            "protection.backup_config_reset",
        }
        or node_task.status
        not in {
            NodeTask.Status.SUCCESS,
            NodeTask.Status.FAILED,
            NodeTask.Status.TIMEOUT,
            NodeTask.Status.CANCELED,
        }
        or node_task.parent_task_id is None
    ):
        return False
    if node_task.status == NodeTask.Status.FAILED and node_task.accepted_at is None:
        # The dispatching Worker can safely choose Controller fallback without
        # racing a continuation for a command that never reached the Agent.
        return False
    parent = Task.objects.filter(
        pk=node_task.parent_task_id,
        status__in=[Task.Status.PENDING, Task.Status.RUNNING],
    ).first()
    if parent is None:
        return False
    request = parent.request_payload if isinstance(parent.request_payload, dict) else {}
    if parent.task_type == Task.Type.SNAPSHOT_DELETE:
        source_snapshot_id = int(request.get("source_snapshot_id") or 0)
        if source_snapshot_id <= 0:
            return False
        from apps.protection.tasks.snapshot_delete import execute_snapshot_delete_task

        transaction.on_commit(
            lambda: execute_snapshot_delete_task.apply_async(
                kwargs={
                    "organization_id": int(parent.organization_id),
                    "task_uuid": str(parent.task_uuid),
                    "source_snapshot_id": source_snapshot_id,
                },
                countdown=1,
            )
        )
        return True
    if parent.task_type == Task.Type.BACKUP_CONFIG_RESET:
        source_type = str(request.get("source_type") or "").strip()
        source_ref_id = int(request.get("source_ref_id") or 0)
        if not source_type or source_ref_id <= 0:
            return False
        from apps.protection.tasks.backup_config_reset import (
            execute_backup_config_reset_task,
        )

        transaction.on_commit(
            lambda: execute_backup_config_reset_task.apply_async(
                kwargs={
                    "organization_id": int(parent.organization_id),
                    "task_uuid": str(parent.task_uuid),
                    "source_type": source_type,
                    "source_ref_id": source_ref_id,
                },
                countdown=1,
            )
        )
        return True
    return False


def run_snapshot_delete(
    *,
    organization_id: int,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory: BackupSourceSnapshotDirectory,
    repository: Repository,
    kopia_ids: list[str],
    correlation_type: str,
    agent_runner: Callable[..., Any] = run_agent_task_async,
) -> SnapshotDeleteOutcome | AgentTaskSyncResult:
    """Delete snapshots on their writer, falling back to the Controller for S3."""

    if repository.repo_type != Repository.Type.S3:
        return _run_non_s3_snapshot_delete(
            organization_id=organization_id,
            task=task,
            source_snapshot=source_snapshot,
            directory=directory,
            repository=repository,
            kopia_ids=kopia_ids,
            correlation_type=correlation_type,
            agent_runner=agent_runner,
        )

    locator = resolve_snapshot_repository_locator(
        directory=directory,
        repository=repository,
    )
    execution_node = _snapshot_execution_node(
        organization_id=organization_id,
        node_id=locator.writer_node_id,
    )
    failed_before_dispatch = False
    if execution_node is not None and node_is_available_for_work(execution_node):
        repository_access = resolve_snapshot_repository_reader(
            directory=directory,
            repository=repository,
            fallback_node=execution_node,
            source_type=source_snapshot.source_type,
            source_ref_id=source_snapshot.source_ref_id,
        )
        outcome = _resolve_or_dispatch_agent_snapshot_delete(
            organization_id=organization_id,
            task=task,
            node_id=repository_access.node.id,
            payload={
                "repository": repository_access.repository_payload,
                "kopia_snapshot_ids": kopia_ids,
            },
            correlation_type=correlation_type,
            repository_id=repository.id,
            kopia_ids=kopia_ids,
            agent_runner=agent_runner,
        )
        # Once dispatched, keep the existing task result authoritative. Moving
        # a possibly running delete to the Controller could execute it twice.
        failed_before_dispatch = _agent_delete_failed_before_dispatch(outcome)
        if not failed_before_dispatch:
            return outcome

    if (
        execution_node is not None
        and execution_node.availability == Node.Availability.ONLINE
        and not failed_before_dispatch
    ):
        raise ValidationError(
            {"repository_id": f'Snapshot execution node "{execution_node.name}" is busy.'}
        )

    with repository_execution_lock(
        operation="controller-s3-snapshot-delete",
        operation_id=0,
    ) as acquired:
        if not acquired:
            raise ControllerSnapshotDeleteBusy(
                "Controller snapshot cleanup is busy; the task will continue automatically."
            )
        result = delete_s3_snapshots(
            repository,
            snapshot_ids=kopia_ids,
            timeout_seconds=3600,
        )
    failed_count = int(result.get("failed_count") or 0)
    last_error = ""
    if failed_count:
        failed_items = [
            item
            for item in result.get("results", [])
            if isinstance(item, dict) and item.get("status") == "failed"
        ]
        if failed_items:
            last_error = str(failed_items[0].get("error_message") or "")
    return SnapshotDeleteOutcome(
        task=_ControllerTask(
            status="failed" if failed_count else "success",
            last_error=last_error,
        ),
        result=result,
        ok=failed_count == 0,
    )


def _run_non_s3_snapshot_delete(
    *,
    organization_id: int,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory: BackupSourceSnapshotDirectory,
    repository: Repository,
    kopia_ids: list[str],
    correlation_type: str,
    agent_runner: Callable[..., Any],
) -> AgentTaskSyncResult:
    """Keep the established Agent/Proxy path for non-S3 repositories."""

    fallback_node = None
    if not repository_uses_bound_proxy(repository):
        fallback_node = resolve_source_execution_target(
            organization_id=source_snapshot.organization_id,
            source_type=source_snapshot.source_type,
            source_ref_id=source_snapshot.source_ref_id,
        ).node
    repository_access = resolve_snapshot_repository_reader(
        directory=directory,
        repository=repository,
        fallback_node=fallback_node,
        source_type=source_snapshot.source_type,
        source_ref_id=source_snapshot.source_ref_id,
    )
    return _resolve_or_dispatch_agent_snapshot_delete(
        organization_id=organization_id,
        task=task,
        node_id=repository_access.node.id,
        payload={
            "repository": repository_access.repository_payload,
            "kopia_snapshot_ids": kopia_ids,
        },
        correlation_type=correlation_type,
        repository_id=repository.id,
        kopia_ids=kopia_ids,
        agent_runner=agent_runner,
    )


def _resolve_or_dispatch_agent_snapshot_delete(
    *,
    organization_id: int,
    task: Task,
    node_id: int,
    payload: dict[str, Any],
    correlation_type: str,
    repository_id: int,
    kopia_ids: list[str],
    agent_runner: Callable[..., Any],
) -> AgentTaskSyncResult:
    operation_key = _snapshot_delete_operation_key(
        task=task,
        node_id=node_id,
        repository_id=repository_id,
        kopia_ids=kopia_ids,
    )
    node_task = (
        NodeTask.objects.filter(
            organization_id=organization_id,
            parent_task=task,
            node_id=node_id,
            kind="snapshot.delete",
            correlation_type=correlation_type,
            correlation_id=operation_key,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if node_task is None:
        handle = agent_runner(
            organization_id=organization_id,
            node_id=node_id,
            kind="snapshot.delete",
            payload=payload,
            persisted_payload={
                "repository_id": int(repository_id),
                "kopia_snapshot_ids": sorted(
                    {str(value) for value in kopia_ids if str(value).strip()}
                ),
                "snapshot_delete_operation_key": operation_key,
            },
            correlation_type=correlation_type,
            correlation_id=operation_key,
            parent_task=task,
        )
        if isinstance(handle, AgentTaskSyncResult) or (
            hasattr(handle, "timed_out") and hasattr(handle, "ok")
        ):
            return handle
        node_task = handle.task
        node_task.refresh_from_db()
    if node_task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
        raise AgentSnapshotDeletePending(
            "Agent snapshot cleanup is running; the task will continue automatically."
        )
    return AgentTaskSyncResult(
        task=node_task,
        stream_message=None,
        timed_out=node_task.status == NodeTask.Status.TIMEOUT,
    )


def _snapshot_delete_operation_key(
    *, task: Task, node_id: int, repository_id: int, kopia_ids: list[str]
) -> str:
    identity = "\n".join(
        [
            str(node_id),
            str(repository_id),
            *sorted({str(value).strip() for value in kopia_ids if str(value).strip()}),
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"{task.task_uuid}:{int(task.retry_count or 0)}:{digest}"


def _snapshot_execution_node(
    *,
    organization_id: int,
    node_id: int | None,
) -> Node | None:
    if not node_id:
        return None
    node = Node.all_objects.filter(
        organization_id=organization_id,
        id=node_id,
    ).first()
    if (
        node is None
        or node.is_deleted
        or node.role not in {NodeRole.AGENT, NodeRole.PROXY}
    ):
        return None
    return node


def _agent_delete_failed_before_dispatch(outcome: AgentTaskSyncResult) -> bool:
    """Return whether delivery failed before the Agent could receive the task."""

    marker = object()
    dispatched_at = getattr(outcome.task, "dispatched_at", marker)
    accepted_at = getattr(outcome.task, "accepted_at", None)
    return (
        outcome.task.status in {NodeTask.Status.FAILED, NodeTask.Status.TIMEOUT}
        and dispatched_at is None
        and accepted_at is None
    )


__all__ = [
    "AgentSnapshotDeletePending",
    "ControllerSnapshotDeleteBusy",
    "SnapshotDeleteOutcome",
    "queue_snapshot_delete_result_followup",
    "run_snapshot_delete",
    "snapshot_delete_agent_work_active",
]
