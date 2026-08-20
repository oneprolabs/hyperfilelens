from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.db.models import Q
from django.utils import timezone

from apps.node.models import Node, NodeTask
from apps.node.services.interface import cancel_agent_task, run_agent_task_async
from apps.storage.repositories.models import RepositoryTask
from apps.task.models import Task
from apps.task.services.recovery import (
    RecoveryDecision,
    RecoveryPlan,
    record_recovery_decision,
)


class RepositoryAgentOperationError(RuntimeError):
    def __init__(self, message: str, *, result: dict[str, Any] | None = None):
        super().__init__(message)
        self.result = dict(result or {})


class RepositoryAgentOperationTimeout(TimeoutError):
    pass


class RepositoryAgentOperationStateUnknown(RepositoryAgentOperationError):
    pass


@dataclass(frozen=True)
class RepositoryAgentOperationResult:
    waiting: bool
    node_task_id: UUID | None
    result: dict[str, Any]


def queue_repository_agent_result_followup(*, node_task: NodeTask) -> bool:
    """Queue repository parents affected by one terminal Agent result."""
    if node_task.kind != "repository.operation":
        return False
    if node_task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
        return False
    if node_task.correlation_type not in {
        "repository_cleanup",
        "repository_operation",
    }:
        return False

    relation = Q(remote_task_id=node_task.id)
    try:
        correlation_uuid = UUID(str(node_task.correlation_id))
    except (TypeError, ValueError):
        correlation_uuid = None
    if correlation_uuid is not None:
        relation |= Q(task__task_uuid=correlation_uuid)

    repository_task_ids = list(
        RepositoryTask.objects.filter(
            relation,
            repository__organization_id=node_task.organization_id,
            task__status__in={Task.Status.PENDING, Task.Status.RUNNING},
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    if not repository_task_ids:
        return False

    from apps.storage.tasks import execute_repository_operation

    for repository_task_id in repository_task_ids:
        execute_repository_operation.apply_async(
            kwargs={"repository_task_id": repository_task_id},
            countdown=1,
        )
    return True


def _related_node_task(
    *,
    repository_task: RepositoryTask,
    node: Node,
    correlation_type: str,
) -> NodeTask | None:
    if repository_task.remote_task_id:
        task = NodeTask.objects.filter(
            pk=repository_task.remote_task_id,
            organization_id=repository_task.repository.organization_id,
            node_id=node.id,
            kind="repository.operation",
        ).first()
        if task is not None:
            return task
    return (
        NodeTask.objects.filter(
            organization_id=repository_task.repository.organization_id,
            node_id=node.id,
            kind="repository.operation",
            correlation_type=correlation_type,
            correlation_id=str(repository_task.task.task_uuid),
        )
        .order_by("-created_at", "-id")
        .first()
    )


def resolve_existing_repository_agent_operation(
    *,
    repository_task: RepositoryTask,
    correlation_type: str,
    timeout_seconds: int,
) -> RepositoryAgentOperationResult | None:
    """Resolve one already-dispatched Agent operation without dispatching.

    A destructive parent must observe the durable child task through every
    state transition. Querying terminal and in-flight states separately leaves
    a race where the Agent can delete ownership evidence between queries.
    """
    remote_query = NodeTask.objects.filter(
        organization_id=repository_task.repository.organization_id,
        kind="repository.operation",
        correlation_type=correlation_type,
        correlation_id=str(repository_task.task.task_uuid),
    )
    if repository_task.remote_task_id:
        remote_query = remote_query.filter(pk=repository_task.remote_task_id)
    node_task = remote_query.order_by("-created_at", "-id").first()
    if node_task is None:
        if repository_task.remote_task_id:
            raise RepositoryAgentOperationStateUnknown(
                "The durable Agent task referenced by the repository operation "
                "could not be found."
            )
        return None

    payload = node_task.payload if isinstance(node_task.payload, dict) else {}
    if (
        str(payload.get("repository_id") or "") != str(repository_task.repository_id)
        or payload.get("operation_type") != repository_task.operation_type
    ):
        raise RepositoryAgentOperationStateUnknown(
            "The durable Agent task does not match this repository operation."
        )

    recovered = repository_task.remote_task_id is None
    if repository_task.remote_task_id != node_task.id:
        RepositoryTask.objects.filter(pk=repository_task.id).update(
            remote_task_id=node_task.id
        )
        repository_task.remote_task_id = node_task.id
    if recovered:
        record_recovery_decision(
            task=repository_task.task,
            plan=RecoveryPlan(
                decision=RecoveryDecision.RESUME,
                reason="Recovered the durable Agent task after control-plane interruption.",
                evidence={
                    "node_task_id": str(node_task.id),
                    "node_task_status": node_task.status,
                    "correlation_type": correlation_type,
                },
            ),
        )
    return _resolve_node_task_result(
        node_task=node_task,
        timeout_seconds=timeout_seconds,
    )


def resolve_or_dispatch_repository_agent_operation(
    *,
    repository_task: RepositoryTask,
    node: Node,
    payload: dict[str, Any],
    persisted_payload: dict[str, Any] | None = None,
    correlation_type: str,
    timeout_seconds: int,
    allow_dispatch: bool = True,
) -> RepositoryAgentOperationResult:
    node_task = _related_node_task(
        repository_task=repository_task,
        node=node,
        correlation_type=correlation_type,
    )
    recovered = node_task is not None and repository_task.remote_task_id is None
    if node_task is None:
        if not allow_dispatch:
            raise RepositoryAgentOperationStateUnknown(
                "Repository operation was interrupted after dispatch, but no durable Agent task could be found."
            )
        handle = run_agent_task_async(
            organization_id=repository_task.repository.organization_id,
            node_id=node.id,
            kind="repository.operation",
            payload=payload,
            persisted_payload=persisted_payload,
            correlation_type=correlation_type,
            correlation_id=str(repository_task.task.task_uuid),
        )
        node_task = handle.task

    if repository_task.remote_task_id != node_task.id:
        RepositoryTask.objects.filter(pk=repository_task.id).update(
            remote_task_id=node_task.id
        )
        repository_task.remote_task_id = node_task.id

    if recovered:
        record_recovery_decision(
            task=repository_task.task,
            plan=RecoveryPlan(
                decision=RecoveryDecision.RESUME,
                reason="Recovered the durable Agent task after control-plane interruption.",
                evidence={
                    "node_task_id": str(node_task.id),
                    "node_task_status": node_task.status,
                    "correlation_type": correlation_type,
                },
            ),
        )

    return _resolve_node_task_result(
        node_task=node_task,
        timeout_seconds=timeout_seconds,
    )


def _resolve_node_task_result(
    *,
    node_task: NodeTask,
    timeout_seconds: int,
) -> RepositoryAgentOperationResult:
    if node_task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}:
        deadline = node_task.created_at + timedelta(
            seconds=max(1, int(timeout_seconds))
        )
        if deadline <= timezone.now():
            cancel_agent_task(
                task_id=node_task.id,
                reason="repository operation exceeded its recovery execution timeout",
            )
            raise RepositoryAgentOperationTimeout(
                "Repository owner did not finish the operation before the execution timeout."
            )
        return RepositoryAgentOperationResult(
            waiting=True,
            node_task_id=node_task.id,
            result={},
        )

    if node_task.status == NodeTask.Status.SUCCESS:
        return RepositoryAgentOperationResult(
            waiting=False,
            node_task_id=node_task.id,
            result=dict(node_task.result or {}),
        )
    if node_task.status == NodeTask.Status.TIMEOUT:
        raise RepositoryAgentOperationTimeout(
            node_task.last_error or "Repository owner operation timed out."
        )
    raise RepositoryAgentOperationError(
        node_task.last_error
        or f"Repository owner operation ended with status {node_task.status}.",
        result=node_task.result,
    )
