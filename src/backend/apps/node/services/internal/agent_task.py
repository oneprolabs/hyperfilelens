"""
Cross-app Agent task orchestration (async dispatch vs sync wait).

- **Async**: return ``task_id`` immediately; callers poll via ``selectors``.
- **Sync**: block on ``task_stream:{task_id}`` until terminal or timeout.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.db import transaction

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.exceptions import AgentTaskNotFoundError, NodeNotFoundError
from apps.node.models import Node, NodeTask
from apps.node.selectors.internal.node_query import node_by_id
from apps.node.services.internal import redis_store
from apps.node.services.internal.agent_log import task_log_context
from apps.node.services.internal.task import (
    cancel_task,
    create_agent_task,
    deliver_agent_task,
    dispatch_task,
    protect_task_delivery_payload,
)

if TYPE_CHECKING:
    from apps.task.models import Task

logger = logging.getLogger(__name__)

_TERMINAL_STATUSES = frozenset(
    {
        NodeTask.Status.SUCCESS,
        NodeTask.Status.FAILED,
        NodeTask.Status.TIMEOUT,
        NodeTask.Status.CANCELED,
    },
)
_TASK_SYNC_DB_POLL_SECONDS = 5


@dataclass(frozen=True)
class AgentTaskHandle:
    """Async dispatch result — poll with ``selectors.get_node_task*``."""

    task_id: str
    task: NodeTask

    @property
    def status(self) -> str:
        return self.task.status


@dataclass(frozen=True)
class AgentTaskSyncResult:
    """Sync wait result — ``task`` is authoritative (PostgreSQL)."""

    task: NodeTask
    stream_message: dict[str, Any] | None
    timed_out: bool

    @property
    def task_id(self) -> str:
        return str(self.task.id)

    @property
    def result(self) -> dict[str, Any]:
        return dict(self.task.result or {})

    @property
    def ok(self) -> bool:
        return self.task.status == NodeTask.Status.SUCCESS


def _resolve_org_and_node(
    *,
    organization_id: int | None = None,
    org: Organization | None = None,
    node_id: int,
) -> tuple[Organization, Node]:
    if org is None:
        if organization_id is None:
            raise ValueError("org or organization_id is required")
        org = Organization.objects.filter(
            pk=organization_id,
            is_active=True,
        ).first()
        if org is None:
            raise NodeNotFoundError("organization not found")
    node = node_by_id(org=org, node_id=node_id)
    if node is None:
        raise NodeNotFoundError(f"node {node_id} not found for organization")
    return org, node


def run_agent_task_async(
    *,
    organization_id: int | None = None,
    org: Organization | None = None,
    node_id: int,
    kind: str,
    payload: dict | None = None,
    persisted_payload: dict | None = None,
    correlation_type: str = "",
    correlation_id: str = "",
    requesting_organization_id: int | None = None,
    parent_task: Task | None = None,
) -> AgentTaskHandle:
    """
    Long-running work: dispatch and return immediately; caller polls status.

    Hot progress: ``selectors.get_node_task_runtime_info(task_id=...)``.
    Terminal state: ``selectors.get_node_task*`` / ``get_node_task_for_org``.
    """
    org, node = _resolve_org_and_node(
        organization_id=organization_id,
        org=org,
        node_id=node_id,
    )
    logger.info(
        "agent task async dispatch %s",
        task_log_context(
            node_id=node.id,
            kind=kind,
            correlation_type=correlation_type,
            correlation_id=correlation_id,
        ),
    )
    task_payload = payload
    if persisted_payload is not None:
        task_payload = protect_task_delivery_payload(
            delivery_payload=payload or {},
            persisted_payload=persisted_payload,
        )
    task = dispatch_task(
        org=org,
        node=node,
        kind=kind,
        payload=task_payload,
        correlation_type=correlation_type,
        correlation_id=correlation_id,
        requesting_organization_id=requesting_organization_id,
        parent_task=parent_task,
    )
    return AgentTaskHandle(task_id=str(task.id), task=task)


def wait_for_agent_task(
    *,
    task_id: uuid.UUID | str,
    timeout_seconds: int | None = None,
    on_stream_message: Callable[[dict[str, Any]], None] | None = None,
) -> AgentTaskSyncResult:
    """
    Block on Redis ``task_stream`` until a terminal task result or timeout.

    Authoritative status is always reloaded from PostgreSQL after the wait.
    """
    timeout = (
        int(timeout_seconds)
        if timeout_seconds is not None
        else node_conf.TASK_SYNC_WAIT_SECONDS
    )
    timeout = max(1, timeout)
    waiter_ttl = timeout + node_conf.TASK_STREAM_TTL_GRACE_SECONDS
    with redis_store.task_stream_waiter(
        task_id=str(task_id),
        ttl_seconds=waiter_ttl,
    ):
        return _wait_for_agent_task(
            task_id=task_id,
            timeout=timeout,
            on_stream_message=on_stream_message,
        )


def _wait_for_agent_task(
    *,
    task_id: uuid.UUID | str,
    timeout: int,
    on_stream_message: Callable[[dict[str, Any]], None] | None,
) -> AgentTaskSyncResult:
    """Wait with PostgreSQL as authority after a caller registers its lease."""
    deadline = time.monotonic() + timeout
    last_stream_message: dict[str, Any] | None = None

    while True:
        task = NodeTask.objects.filter(pk=task_id).first()
        if task is None:
            raise AgentTaskNotFoundError(f"task {task_id} not found")
        if task.status in _TERMINAL_STATUSES:
            return AgentTaskSyncResult(
                task=task,
                stream_message=last_stream_message,
                timed_out=False,
            )

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.warning(
                "agent task sync wait timed out %s task_status=%s wait_seconds=%s",
                task_log_context(
                    node_id=task.node_id, task_id=str(task_id), kind=task.kind
                ),
                task.status,
                timeout,
            )
            return AgentTaskSyncResult(
                task=task,
                stream_message=last_stream_message,
                timed_out=True,
            )

        wait_seconds = max(1, min(_TASK_SYNC_DB_POLL_SECONDS, int(remaining + 0.999)))
        wait_started = time.monotonic()
        stream_message = redis_store.bpop_task_stream(
            task_id=str(task_id),
            timeout_seconds=wait_seconds,
        )
        if stream_message is None:
            task = NodeTask.objects.filter(pk=task_id).first()
            if task is None:
                raise AgentTaskNotFoundError(f"task {task_id} not found")
            if task.status in _TERMINAL_STATUSES:
                return AgentTaskSyncResult(
                    task=task,
                    stream_message=last_stream_message,
                    timed_out=False,
                )
            # A healthy BLPOP already waited. If Redis failed immediately,
            # preserve the normal database polling cadence instead of spinning.
            elapsed = time.monotonic() - wait_started
            if elapsed < wait_seconds:
                time.sleep(wait_seconds - elapsed)
            continue
        last_stream_message = stream_message
        if on_stream_message is not None:
            try:
                on_stream_message(stream_message)
            except Exception:
                logger.exception(
                    "agent task stream callback failed task_id=%s", task_id
                )
        stream_status = str(stream_message.get("status") or "").strip().lower()
        if stream_status in {
            "success",
            "succeeded",
            "ok",
            "failed",
            "canceled",
            "cancelled",
            "timeout",
        }:
            task = NodeTask.objects.filter(pk=task_id).first()
            if task is None:
                raise AgentTaskNotFoundError(f"task {task_id} not found")
            if task.status in _TERMINAL_STATUSES:
                return AgentTaskSyncResult(
                    task=task,
                    stream_message=stream_message,
                    timed_out=False,
                )


def run_agent_task_sync(
    *,
    organization_id: int | None = None,
    org: Organization | None = None,
    node_id: int,
    kind: str,
    payload: dict | None = None,
    persisted_payload: dict | None = None,
    correlation_type: str = "",
    correlation_id: str = "",
    requesting_organization_id: int | None = None,
    parent_task: Task | None = None,
    wait_timeout_seconds: int | None = None,
    on_stream_message: Callable[[dict[str, Any]], None] | None = None,
) -> AgentTaskSyncResult:
    """
    Short tasks (e.g. browse directory): dispatch then block on ``task_stream``.

    Delivery runs in the same request after the task row is committed so the
    caller receives a single synchronous outcome.
    """
    org, node = _resolve_org_and_node(
        organization_id=organization_id,
        org=org,
        node_id=node_id,
    )
    logger.info(
        "agent task sync dispatch %s wait_seconds=%s",
        task_log_context(
            node_id=node.id,
            kind=kind,
            correlation_type=correlation_type,
            correlation_id=correlation_id,
        ),
        wait_timeout_seconds
        if wait_timeout_seconds is not None
        else node_conf.TASK_SYNC_WAIT_SECONDS,
    )
    with transaction.atomic():
        task = create_agent_task(
            org=org,
            node=node,
            kind=kind,
            payload=persisted_payload if persisted_payload is not None else payload,
            correlation_type=correlation_type,
            correlation_id=correlation_id,
            requesting_organization_id=requesting_organization_id,
            parent_task=parent_task,
        )
        task_id = task.id

    timeout = max(
        1,
        int(wait_timeout_seconds)
        if wait_timeout_seconds is not None
        else node_conf.TASK_SYNC_WAIT_SECONDS,
    )
    with redis_store.task_stream_waiter(
        task_id=str(task_id),
        ttl_seconds=timeout + node_conf.TASK_STREAM_TTL_GRACE_SECONDS,
    ):
        task = deliver_agent_task(
            task=task,
            delivery_payload=payload if persisted_payload is not None else None,
        )
        outcome = _wait_for_agent_task(
            task_id=task_id,
            timeout=timeout,
            on_stream_message=on_stream_message,
        )
    ctx = task_log_context(node_id=node.id, task_id=str(task_id), kind=kind)
    if outcome.timed_out:
        logger.warning(
            "agent task sync finished (timeout) %s task_status=%s last_error=%s",
            ctx,
            outcome.task.status,
            (outcome.task.last_error or "")[:500],
        )
    elif outcome.ok:
        logger.info(
            "agent task sync finished (ok) %s task_status=%s", ctx, outcome.task.status
        )
    else:
        logger.warning(
            "agent task sync finished (failed) %s task_status=%s last_error=%s",
            ctx,
            outcome.task.status,
            (outcome.task.last_error or "")[:500],
        )
    return outcome


def cancel_agent_task(
    *,
    task_id: uuid.UUID | str,
    reason: str = "canceled",
) -> NodeTask | None:
    """Request Agent to stop and mark task canceled when still active."""
    return cancel_task(task_id=task_id, reason=reason)
