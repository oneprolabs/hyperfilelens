from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError

from apps.node.models import Node, NodeTask
from apps.node.services.interface import AgentTaskSyncResult
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


def run_snapshot_delete(
    *,
    organization_id: int,
    task: Task,
    source_snapshot: BackupSourceSnapshot,
    directory: BackupSourceSnapshotDirectory,
    repository: Repository,
    kopia_ids: list[str],
    correlation_type: str,
    agent_runner: Callable[..., AgentTaskSyncResult],
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
        outcome = agent_runner(
            organization_id=organization_id,
            node_id=repository_access.node.id,
            kind="snapshot.delete",
            payload={
                "repository": repository_access.repository_payload,
                "kopia_snapshot_ids": kopia_ids,
            },
            correlation_type=correlation_type,
            correlation_id=str(task.task_uuid),
            parent_task=task,
            wait_timeout_seconds=3600,
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
    agent_runner: Callable[..., AgentTaskSyncResult],
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
    return agent_runner(
        organization_id=organization_id,
        node_id=repository_access.node.id,
        kind="snapshot.delete",
        payload={
            "repository": repository_access.repository_payload,
            "kopia_snapshot_ids": kopia_ids,
        },
        correlation_type=correlation_type,
        correlation_id=str(task.task_uuid),
        parent_task=task,
        wait_timeout_seconds=3600,
    )


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
    return (
        not outcome.timed_out
        and outcome.task.status == NodeTask.Status.FAILED
        and dispatched_at is None
    )


__all__ = ["SnapshotDeleteOutcome", "run_snapshot_delete"]
