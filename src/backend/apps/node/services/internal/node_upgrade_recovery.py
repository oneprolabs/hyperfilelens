"""Verify late reconnects without reopening a sealed upgrade task."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from copy import copy
from datetime import timedelta

from django.db import transaction
from django.db.models import OuterRef, Q, QuerySet, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.audit.constants import AuditResult
from apps.audit.services.interface import write_audit_log
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole

_RECOVERY_KEY = "upgrade_recovery"
_FAILURE_CODE = "AGENT_RECONNECT_TIMEOUT"
_SUCCESS_STATUSES = {"success", "succeeded", "ok"}
logger = logging.getLogger(__name__)


def _latest_lifecycle_tasks(*, node_id: int | OuterRef) -> QuerySet[NodeTask]:
    return NodeTask.objects.filter(
        node_id=node_id,
        correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
        kind__in=("agent.upgrade", "agent.uninstall"),
    ).order_by("-created_at", "-pk")


def is_reconnect_timeout_upgrade(task: NodeTask) -> bool:
    """Identify only formal detached upgrades sealed for reconnect timeout."""
    from .node_lifecycle import _is_detached_lifecycle_task

    return (
        task.kind == "agent.upgrade"
        and task.correlation_type == node_conf.LIFECYCLE_CORRELATION_TYPE
        and task.correlation_id == f"upgrade:{task.node_id}"
        and task.status in {NodeTask.Status.FAILED, NodeTask.Status.TIMEOUT}
        and (task.result or {}).get("failure_code") == _FAILURE_CODE
        and _is_detached_lifecycle_task(task)
    )


def _host_succeeded(task: NodeTask) -> bool:
    result = task.result or {}
    late = result.get("late_result")
    if (
        isinstance(late, dict)
        and str(late.get("status") or "").lower() in _SUCCESS_STATUSES
    ):
        host = late.get("result")
        if isinstance(host, dict) and host.get("host_upgrade_status") == "success":
            return True
    return result.get("host_upgrade_status") == "success"


def upgrade_recovery_verified(task: NodeTask) -> bool:
    """A durable recovery belongs only to this task and its requested build."""
    from .node_lifecycle import _target_commit_from_task, _target_version_from_task

    if not is_reconnect_timeout_upgrade(task):
        return False
    recovery = (task.result or {}).get(_RECOVERY_KEY)
    if not isinstance(recovery, dict):
        return False
    target_version = _target_version_from_task(task)
    target_commit = _target_commit_from_task(task)
    session = recovery.get("reconnect_session_id")
    return bool(
        recovery.get("verified_at")
        and recovery.get("task_id") == str(task.pk)
        and target_version
        and recovery.get("observed_agent_version") == target_version
        and (
            not target_commit or recovery.get("observed_agent_commit") == target_commit
        )
        and session
        and recovery.get("inventory_session_id") == session
        and recovery.get("detached_session_id")
        and recovery["detached_session_id"] != session
    )


@transaction.atomic
def project_reconnect_timeout_upgrade(*, node_task: NodeTask) -> None:
    """Fence terminal recovery projections against newer lifecycle operations."""
    from .task import _update_node_lifecycle_status

    # This entry can be called without an enclosing transaction, or while a
    # result handler holds Task first. As in recovery advancement, skip busy
    # rows rather than introducing a blocking reverse-order lock acquisition.
    node = (
        Node.objects.select_for_update(skip_locked=True, of=("self",))
        .filter(pk=node_task.node_id, organization_id=node_task.organization_id)
        .first()
    )
    if node is None:
        return
    task = (
        NodeTask.objects.select_for_update(skip_locked=True, of=("self",))
        .filter(pk=node_task.pk, node_id=node.pk, organization_id=node.organization_id)
        .first()
    )
    if task is None or not is_reconnect_timeout_upgrade(task):
        return
    latest_id = (
        _latest_lifecycle_tasks(node_id=node.pk).values_list("pk", flat=True).first()
    )
    if latest_id != task.pk:
        return
    recovered = upgrade_recovery_verified(task)
    if recovered and node.status not in {
        Node.Status.ACTIVE,
        Node.Status.UPGRADE_FAILED,
    }:
        return
    _update_node_lifecycle_status(
        node_id=node.pk,
        status=Node.Status.ACTIVE if recovered else Node.Status.UPGRADE_FAILED,
    )


def _candidate(node: Node) -> NodeTask | None:
    if (
        node.is_deleted
        or node.status != Node.Status.UPGRADE_FAILED
        or node.role not in {NodeRole.AGENT, NodeRole.PROXY}
    ):
        return None
    task = _latest_lifecycle_tasks(node_id=node.pk).first()
    if (
        task is None
        or task.organization_id != node.organization_id
        or not is_reconnect_timeout_upgrade(task)
        or upgrade_recovery_verified(task)
    ):
        return None
    return task


def _save_recovery(task: NodeTask, recovery: dict) -> None:
    # Do not use complete_task/_sync_task_info: the terminal status is immutable
    # and an ordinary failure projection would undo the node's recovery.
    task.result = {**dict(task.result or {}), _RECOVERY_KEY: recovery}
    task.save(update_fields=["result", "updated_at"])


def record_failed_upgrade_observation(
    *,
    node: Node,
    session_id: str = "",
    inventory: dict | None = None,
    disconnected: bool = False,
) -> bool:
    """Collect evidence even if host success has not arrived yet.

    Called from the existing task-row transaction. No Node row is written or
    locked here, including on WebSocket result delivery.
    """
    from .node_lifecycle import _current_agent_session

    candidate = _candidate(node)
    if candidate is None:
        return False
    task = NodeTask.objects.select_for_update().get(pk=candidate.pk)
    if (
        not is_reconnect_timeout_upgrade(task)
        or upgrade_recovery_verified(task)
        or _latest_lifecycle_tasks(node_id=node.pk).values_list("pk", flat=True).first()
        != task.pk
    ):
        return False
    recovery = dict((task.result or {}).get(_RECOVERY_KEY) or {})
    if disconnected:
        if "verify_started_at" not in recovery:
            return False
        recovery.pop("verify_started_at")
        _save_recovery(task, recovery)
        return True
    if not session_id or _current_agent_session(node_id=node.pk) != session_id:
        return False
    baseline = str(
        (task.result or {}).get("detached_session_id")
        or (task.payload or {}).get("pre_upgrade_session_id")
        or ""
    ).strip()
    if not baseline or baseline == session_id:
        return False
    previous = dict(recovery)
    if recovery.get("reconnect_session_id") != session_id:
        recovery = {
            "task_id": str(task.pk),
            "detached_session_id": baseline,
            "reconnect_session_id": session_id,
            "reconnect_observed_at": timezone.now().isoformat(),
        }
    if inventory is not None:
        version = str(inventory.get("agent_version") or "").strip()
        commit = str(inventory.get("agent_commit") or "").strip().lower()
        same_session = recovery.get("inventory_session_id") == session_id
        if same_session:
            previous_version = recovery.get("observed_agent_version", "")
            if not version:
                version = previous_version
                commit = recovery.get("observed_agent_commit", "")
            elif version == previous_version and not commit:
                commit = recovery.get("observed_agent_commit", "")
        identity = (session_id, version, commit)
        previous_identity = (
            recovery.get("inventory_session_id"),
            recovery.get("observed_agent_version"),
            recovery.get("observed_agent_commit"),
        )
        if identity != previous_identity:
            recovery.update(
                inventory_session_id=session_id,
                inventory_observed_at=timezone.now().isoformat(),
                observed_agent_version=version,
                observed_agent_commit=commit,
            )
            recovery.pop("verify_started_at", None)
    if recovery == previous:
        return False
    _save_recovery(task, recovery)
    return True


@transaction.atomic
def advance_failed_upgrade_recovery(
    *, node_id: int, organization_id: int | None
) -> bool:
    """Repair only current state, under the same Node fence as new operations."""
    from .node_lifecycle import _upgrade_verify_ready
    from .task import _update_node_lifecycle_status

    # Existing result handlers take Task before projecting Node. Never wait
    # for either row here: a conflicting holder is retried by the periodic
    # sweep rather than creating a Node -> Task -> Node lock cycle.
    node = (
        Node.objects.select_for_update(skip_locked=True, of=("self",))
        .filter(
            pk=node_id,
            organization_id=organization_id,
            status=Node.Status.UPGRADE_FAILED,
            role__in=(NodeRole.AGENT, NodeRole.PROXY),
        )
        .first()
    )
    if node is None:
        return False
    candidate = _candidate(node)
    if candidate is None:
        return False
    task = (
        NodeTask.objects.select_for_update(skip_locked=True, of=("self",))
        .filter(pk=candidate.pk)
        .first()
    )
    if (
        task is None
        or not is_reconnect_timeout_upgrade(task)
        or upgrade_recovery_verified(task)
    ):
        return False
    recovery = dict((task.result or {}).get(_RECOVERY_KEY) or {})
    # Reuse the normal session/build readiness predicates, but never pass this
    # detached copy into completion or normal projection.
    evidence = copy(task)
    evidence.result = {
        **recovery,
        "mode": "local_detached",
        "host_upgrade_status": "success" if _host_succeeded(task) else "",
    }
    ready = (
        recovery.get("task_id") == str(task.pk)
        and _host_succeeded(task)
        and _upgrade_verify_ready(node=node, task=evidence)
    )
    if not ready:
        if recovery.pop("verify_started_at", None):
            _save_recovery(task, recovery)
        return False
    now = timezone.now()
    raw_started = recovery.get("verify_started_at")
    started = parse_datetime(raw_started) if isinstance(raw_started, str) else None
    if started is None or timezone.is_naive(started) or started > now:
        recovery["verify_started_at"] = now.isoformat()
        _save_recovery(task, recovery)
        return False
    if now - started < timedelta(seconds=node_conf.UPGRADE_STABLE_SECONDS):
        return False
    recovery["verified_at"] = now.isoformat()
    _save_recovery(task, recovery)
    _update_node_lifecycle_status(node_id=node.pk, status=Node.Status.ACTIVE)
    write_audit_log(
        organization=node.organization,
        action="node.lifecycle.upgrade.recovered",
        target_type="node",
        target_id=str(node.pk),
        resource_type="node",
        resource_id=str(node.pk),
        resource_name=node.name,
        result=AuditResult.SUCCESS,
        metadata={
            "node_task_id": str(task.pk),
            "failure_code": _FAILURE_CODE,
            "failed_at": (task.result or {}).get("failed_at"),
            "recovered_at": recovery["verified_at"],
            "observed_agent_version": recovery["observed_agent_version"],
            "observed_agent_commit": recovery["observed_agent_commit"],
            "session_id": recovery["reconnect_session_id"],
        },
    )
    return True


def queue_failed_upgrade_recovery(*, node_id: int) -> None:
    """Disposable post-commit wake-up; the periodic sweep is authoritative."""
    from apps.node.services.internal import redis_store
    from apps.node.tasks.lifecycle import advance_node_lifecycle_for_node

    try:
        if not redis_store.claim_lifecycle_advance_event(node_id=node_id):
            return
        advance_node_lifecycle_for_node.apply_async(
            kwargs={"node_id": node_id},
            expires=node_conf.LIFECYCLE_ADVANCE_EXPIRE_SECONDS,
        )
    except Exception:
        logger.warning(
            "late upgrade recovery wake-up failed node_id=%s", node_id, exc_info=True
        )


def reconnect_timeout_recovery_node_ids() -> Iterator[int]:
    """Stream only latest, success-backed reconnect failures needing recovery."""
    latest = _latest_lifecycle_tasks(node_id=OuterRef("node_id")).values("pk")[:1]
    tasks = (
        NodeTask.objects.filter(
            pk=Subquery(latest),
            kind="agent.upgrade",
            correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
            status__in=(NodeTask.Status.FAILED, NodeTask.Status.TIMEOUT),
            result__failure_code=_FAILURE_CODE,
            node__is_deleted=False,
            node__status=Node.Status.UPGRADE_FAILED,
            node__role__in=(NodeRole.AGENT, NodeRole.PROXY),
        )
        .filter(
            Q(result__host_upgrade_status="success")
            | Q(
                result__late_result__status__in=_SUCCESS_STATUSES,
                result__late_result__result__host_upgrade_status="success",
            )
        )
        .order_by()
        .values_list("node_id", flat=True)
    )
    yield from tasks.iterator(chunk_size=200)
