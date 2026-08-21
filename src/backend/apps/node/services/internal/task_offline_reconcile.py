"""Offline / reconnecting task reconciliation (platform ↔ agent consistency)."""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal.node_registry import (
    CONNECTION_OFFLINE,
    CONNECTION_RECONNECTING,
    agent_connection_status,
)
from apps.node.services.internal import redis_store
from apps.protection import conf as protection_conf
from apps.task.constants import RESTORE_TASK_TYPES, is_restore_task_type
from apps.task.models import Task

logger = logging.getLogger(__name__)

_ACTIVE_NODE_TASK = frozenset({NodeTask.Status.PENDING, NodeTask.Status.RUNNING})
_ACTIVE_PRODUCT_TASK = frozenset({Task.Status.PENDING, Task.Status.RUNNING})
_AGENT_OFFLINE_REASON = "Agent went offline during task execution."


def offline_stale_threshold_seconds() -> int:
    return int(node_conf.NODE_RECONNECT_GRACE_SECONDS) + int(
        node_conf.OFFLINE_TASK_FAIL_SECONDS
    )


def is_node_reconnecting(node: Node) -> bool:
    return agent_connection_status(node) == CONNECTION_RECONNECTING


def is_node_offline_stale(node: Node) -> bool:
    if not redis_store.offline_task_finalization_ready():
        return False
    if agent_connection_status(node) != CONNECTION_OFFLINE:
        return False
    reference = node.last_seen_at or node.updated_at
    if reference is None:
        return True
    return (
        timezone.now() - reference
    ).total_seconds() >= offline_stale_threshold_seconds()


def task_execution_state(*, node: Node | None, task: Task | None) -> str:
    if task is None or str(task.status or "") not in _ACTIVE_PRODUCT_TASK:
        return "idle"
    if node is None:
        return "running"
    connection = agent_connection_status(node)
    if connection == CONNECTION_RECONNECTING:
        return "reconnecting"
    if connection == CONNECTION_OFFLINE:
        return "offline_stale" if is_node_offline_stale(node) else "offline_pending"
    return "running"


def execution_state_blocks_workload(*, node: Node, task: Task) -> bool:
    """True when an active product task should block delete / lifecycle."""
    if str(task.status or "") not in _ACTIVE_PRODUCT_TASK:
        return False
    state = task_execution_state(node=node, task=task)
    return state in {"running", "reconnecting", "offline_pending"}


def sync_platform_tasks_for_node_task(*, node_task: NodeTask) -> None:
    if node_task.correlation_type == "source.connection_probe":
        from apps.source.tasks.connection_probe import (
            project_source_connection_probe,
        )

        project_source_connection_probe(node_task=node_task)
        return
    if node_task.correlation_type == "storage.repository_health":
        from apps.storage.services.internal.repository_health import (
            project_repository_health_from_agent_result,
        )

        project_repository_health_from_agent_result(node_task=node_task)
        return
    if node_task.correlation_type in {
        "protection.snapshot_delete",
        "protection.backup_config_reset",
    }:
        from apps.protection.services.snapshot_delete_execution import (
            queue_snapshot_delete_result_followup,
        )

        queue_snapshot_delete_result_followup(node_task=node_task)
        return
    if node_task.correlation_type == node_conf.LIFECYCLE_CORRELATION_TYPE:
        from apps.node.services.internal.task import project_node_lifecycle_task

        project_node_lifecycle_task(node_task=node_task)
        return
    if node_task.correlation_type == protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE:
        from apps.protection.services.backup_orchestrator import (
            maybe_trigger_backup_advance,
        )

        maybe_trigger_backup_advance(node_task=node_task)
        return
    if node_task.correlation_type == "restore.record":
        from apps.restore.services.restore_progress import (
            maybe_trigger_restore_progress,
        )

        maybe_trigger_restore_progress(node_task=node_task)


def persist_task_execution_state(*, task: Task, node: Node | None) -> None:
    state = task_execution_state(node=node, task=task)
    payload = dict(task.result_payload) if isinstance(task.result_payload, dict) else {}
    if payload.get("execution_state") == state:
        return
    payload["execution_state"] = state
    task.result_payload = payload
    task.save(update_fields=["result_payload", "updated_at"])


@transaction.atomic
def fail_node_task_offline(*, node_task: NodeTask, reason: str | None = None) -> bool:
    if not redis_store.offline_task_finalization_ready():
        logger.info(
            "node task offline finalization held node_task_id=%s node_id=%s",
            node_task.id,
            node_task.node_id,
        )
        return False
    task = (
        NodeTask.objects.select_for_update()
        .filter(pk=node_task.pk, status__in=_ACTIVE_NODE_TASK)
        .first()
    )
    if task is None:
        node_task.refresh_from_db()
        if node_task.status in _ACTIVE_NODE_TASK:
            return False
        sync_platform_tasks_for_node_task(node_task=node_task)
        return False
    if task.status == NodeTask.Status.SUCCESS:
        sync_platform_tasks_for_node_task(node_task=task)
        return False
    message = (reason or _AGENT_OFFLINE_REASON)[:2000]
    task.status = NodeTask.Status.FAILED
    task.last_error = message
    task.save(update_fields=["status", "last_error", "updated_at"])
    logger.warning(
        "node task failed offline node_task_id=%s node_id=%s kind=%s correlation_type=%s correlation_id=%s",
        task.id,
        task.node_id,
        task.kind,
        task.correlation_type,
        task.correlation_id,
    )
    sync_platform_tasks_for_node_task(node_task=task)
    return True


def reconcile_offline_stale_node_tasks(*, limit: int = 100) -> dict[str, int]:
    """Fail active node tasks on offline-stale agent/proxy nodes and advance platform tasks."""
    if not redis_store.offline_task_finalization_ready():
        return {
            "nodes_checked": 0,
            "nodes_stale": 0,
            "node_tasks_failed": 0,
            "offline_stale_threshold_seconds": offline_stale_threshold_seconds(),
            "recovery_hold": True,
        }
    node_ids = list(
        Node.objects.filter(
            role__in=(NodeRole.AGENT, NodeRole.PROXY, NodeRole.GATEWAY),
            is_deleted=False,
        )
        .order_by("last_seen_at", "id")
        .values_list("pk", flat=True)[: max(1, int(limit) * 4)]
    )
    nodes_checked = 0
    nodes_stale = 0
    node_tasks_failed = 0
    for node_id in node_ids:
        node = Node.objects.filter(pk=node_id, is_deleted=False).first()
        if node is None:
            continue
        nodes_checked += 1
        if not is_node_offline_stale(node):
            continue
        nodes_stale += 1
        active_tasks = NodeTask.objects.filter(
            node_id=node.id,
            status__in=_ACTIVE_NODE_TASK,
        ).order_by("updated_at", "id")[: max(1, int(limit))]
        for node_task in active_tasks:
            if fail_node_task_offline(node_task=node_task):
                node_tasks_failed += 1
        if node_tasks_failed >= int(limit):
            break
    summary = {
        "nodes_checked": nodes_checked,
        "nodes_stale": nodes_stale,
        "node_tasks_failed": node_tasks_failed,
        "offline_stale_threshold_seconds": offline_stale_threshold_seconds(),
        "recovery_hold": False,
    }
    if node_tasks_failed:
        logger.info("reconcile_offline_stale_node_tasks %s", summary)
    return summary


def reconcile_execution_state_for_active_tasks(*, limit: int = 200) -> dict[str, int]:
    updated = 0
    tasks = Task.objects.filter(
        status__in=_ACTIVE_PRODUCT_TASK,
        task_type__in=[Task.Type.BACKUP, *RESTORE_TASK_TYPES],
    ).order_by("updated_at", "id")[: max(1, int(limit))]
    for task in tasks:
        node = _execution_node_for_product_task(task=task)
        before = (
            task.result_payload.get("execution_state")
            if isinstance(task.result_payload, dict)
            else None
        )
        persist_task_execution_state(task=task, node=node)
        task.refresh_from_db()
        after = (
            task.result_payload.get("execution_state")
            if isinstance(task.result_payload, dict)
            else None
        )
        if before != after:
            updated += 1
    return {"execution_state_updated": updated, "candidates": len(tasks)}


def _execution_node_for_product_task(*, task: Task) -> Node | None:
    payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    if str(task.task_type or "") == Task.Type.BACKUP:
        if str(payload.get("source_type") or "") != "agent":
            return None
        ref_id = int(payload.get("source_ref_id") or 0)
        if ref_id <= 0:
            return None
        return Node.objects.filter(
            pk=ref_id,
            organization_id=task.organization_id,
            role=NodeRole.AGENT,
        ).first()
    if is_restore_task_type(task.task_type):
        from apps.restore.models import RestoreRecord

        record = RestoreRecord.objects.filter(
            organization_id=task.organization_id,
            task_uuid=task.task_uuid,
        ).first()
        if record is None:
            return None
        return Node.objects.filter(
            pk=int(record.target_execution_node_id or 0),
            organization_id=record.target_execution_organization_id,
            role__in=(NodeRole.AGENT, NodeRole.PROXY, NodeRole.GATEWAY),
        ).first()
    return None


def product_task_blocks_cleanup(*, task: Task) -> bool:
    node = _execution_node_for_product_task(task=task)
    if node is None:
        return str(task.status or "") in _ACTIVE_PRODUCT_TASK
    return execution_state_blocks_workload(node=node, task=task)
