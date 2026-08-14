"""
Agent WebSocket session and uplink for lifecycle and task frames.
"""

from __future__ import annotations

import logging

from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.metrics import TASK_RESULT_RETRANSMISSIONS
from apps.node.services.internal import redis_store
from apps.node.services.internal.agent_log import task_log_context
from apps.node.services.internal.node_naming import (
    resolve_inventory_node_name,
    uniquify_node_name,
)
from apps.node.services.internal.network_inventory import (
    normalize_agent_network_state,
    same_network_inventory,
)
from apps.node.services.interface import (
    accept_task,
    complete_task,
    record_task_progress,
)
from apps.node.services.internal.node_registry import record_node_available
from apps.node.ws.wire import ParsedUplink, WireType
from apps.source.services.internal.agent_host_sync import sync_agent_source_host_by_id

logger = logging.getLogger(__name__)


def on_agent_connected(*, node_id: int, session_id: str, client_ip: str | None = None) -> None:
    redis_store.set_agent_location(agent_id=node_id, session_id=session_id)
    redis_store.touch_ws_instance_alive()
    observed_at = timezone.now()
    updates: dict = {
        "last_seen_at": observed_at,
    }
    if client_ip:
        updates["connection_ip_address"] = client_ip
    Node.objects.filter(pk=node_id).update(**updates)
    record_node_available(node_id=node_id, observed_at=observed_at)
    try:
        sync_agent_source_host_by_id(node_id=node_id)
    except Exception:
        logger.debug("agent source-host sync failed node_id=%s", node_id, exc_info=True)
    logger.info(
        "agent ws connected node_id=%s session=%s client_ip=%s",
        node_id,
        session_id,
        client_ip or "-",
    )
    _schedule_lifecycle_advance(node_id=node_id)


def on_agent_disconnected(*, node_id: int, session_id: str) -> None:
    if not redis_store.clear_agent_location_if_session(
        agent_id=node_id,
        session_id=session_id,
    ):
        logger.info(
            "agent ws disconnected ignored (superseded session) node_id=%s session=%s",
            node_id,
            session_id,
        )
        return

    try:
        sync_agent_source_host_by_id(node_id=node_id)
    except Exception:
        logger.debug("agent source-host sync failed node_id=%s", node_id, exc_info=True)
    logger.info(
        "agent ws disconnected node_id=%s session=%s",
        node_id,
        session_id,
    )
    from apps.node.services.internal.node_lifecycle import record_upgrade_disconnect

    try:
        record_upgrade_disconnect(node_id=node_id)
    except DatabaseError:
        logger.warning(
            "agent upgrade disconnect marker failed node_id=%s",
            node_id,
            exc_info=True,
        )
    _schedule_lifecycle_advance(node_id=node_id)


def _schedule_lifecycle_advance(*, node_id: int) -> None:
    from apps.node.tasks.lifecycle import advance_node_lifecycle_for_node

    advance_node_lifecycle_for_node.delay(node_id=int(node_id))


def handle_uplink(*, node_id: int, message: ParsedUplink) -> NodeTask | None:
    if message.msg_type == WireType.HEARTBEAT:
        _process_heartbeat_followup(node_id=node_id, inventory=message.heartbeat_payload)
        return None

    if message.msg_type in (WireType.TASK_PROGRESS, WireType.TASK_ALIVE):
        _handle_task_progress(node_id=node_id, message=message)
        return None

    if message.msg_type == WireType.TASK_ACCEPTED:
        return accept_task(task_id=message.task_id or "", node_id=node_id)

    if message.msg_type == WireType.TASK_RESULT:
        return _handle_task_result(node_id=node_id, message=message)
    return None


def _inventory_throttle_key(*, node_id: int) -> str:
    return f"heartbeat_inv_throttle:{node_id}"


def _merge_heartbeat_inventory_updates(*, node: Node, inventory: dict) -> dict:
    """Build ``Node.objects.update`` kwargs for inventory snapshot fields (not metrics)."""
    updates: dict = {}
    state = normalize_agent_network_state(inventory)
    inv_only = {k: v for k, v in state.metadata_inventory.items() if k != "metrics"}
    if ver := str(inventory.get("agent_version") or "").strip():
        updates["version"] = ver
    if (
        state.primary_ip_address
        and str(node.ip_address or "") != state.primary_ip_address
    ):
        updates["ip_address"] = state.primary_ip_address
    if state.inventory is not None and not same_network_inventory(
        node.network_inventory,
        state.inventory,
    ):
        updates["network_inventory"] = state.inventory
    if inv_only:
        meta = dict(node.metadata or {})
        meta["inventory"] = {**dict(meta.get("inventory") or {}), **inv_only}
        suggested = resolve_inventory_node_name(node=node, inventory=inv_only)
        if suggested:
            updates["name"] = uniquify_node_name(
                organization_id=node.organization_id,
                name=suggested,
                exclude_node_id=node.id,
            )
        updates["metadata"] = meta
    return updates


@transaction.atomic
def _persist_heartbeat_snapshot(
    *,
    node_id: int,
    inventory: dict | None,
    observed_at,
    merge_inventory: bool,
) -> Node | None:
    """Serialize Node metadata updates shared by heartbeat and monitor ingest."""
    node = Node.objects.select_for_update().filter(pk=node_id).first()
    if node is None:
        return None
    updates: dict = {"last_seen_at": observed_at}
    if merge_inventory and inventory:
        updates.update(
            _merge_heartbeat_inventory_updates(node=node, inventory=inventory)
        )
    Node.objects.filter(pk=node_id).update(**updates)
    return node


def apply_heartbeat_inventory_snapshot(*, node_id: int, inventory: dict | None) -> None:
    """Hot path: persist list/detail inventory fields without waiting for Celery ingest."""
    if not inventory:
        return
    observed_at = timezone.now()
    node = _persist_heartbeat_snapshot(
        node_id=node_id,
        inventory=inventory,
        observed_at=observed_at,
        merge_inventory=True,
    )
    if node is None:
        return
    record_node_available(node_id=node_id, observed_at=observed_at)


def _should_process_full_inventory(*, node_id: int) -> bool:
    r = redis_store.get_redis()
    if r is None:
        return True
    key = _inventory_throttle_key(node_id=node_id)
    if r.exists(key):
        return False
    r.set(key, "1", ex=max(60, int(node_conf.HEARTBEAT_INVENTORY_MIN_INTERVAL_SECONDS)))
    return True


def _process_heartbeat_followup(*, node_id: int, inventory: dict | None = None) -> None:
    redis_store.touch_agent_location(agent_id=node_id)
    redis_store.touch_ws_instance_alive()
    full_inventory = inventory and _should_process_full_inventory(node_id=node_id)
    observed_at = timezone.now()
    node = _persist_heartbeat_snapshot(
        node_id=node_id,
        inventory=inventory,
        observed_at=observed_at,
        # The WebSocket hot path already persisted this snapshot before it
        # entered the queue. Reapplying a delayed payload could overwrite a
        # newer inventory that arrived while the queue was backlogged.
        merge_inventory=False,
    )
    if node is None:
        return
    record_node_available(node_id=node_id, observed_at=observed_at)

    if (
        full_inventory
        and inventory
        and isinstance(inventory.get("metrics"), dict)
        and inventory["metrics"]
    ):
        from apps.monitor.services.internal.node_metrics import ingest_node_monitor_sample

        ingest_node_monitor_sample(node=node, sample=inventory["metrics"])

    if full_inventory:
        try:
            sync_agent_source_host_by_id(node_id=node_id)
        except Exception:
            logger.debug("agent source-host sync failed node_id=%s", node_id, exc_info=True)


def _handle_task_progress(*, node_id: int, message: ParsedUplink) -> None:
    if not message.task_id:
        return
    try:
        task = record_task_progress(
            task_id=message.task_id,
            node_id=node_id,
            progress=message.progress or {},
            alive=message.is_alive,
        )
    except LookupError:
        logger.debug(
            "agent task progress ignored (unknown task) %s",
            task_log_context(node_id=node_id, task_id=message.task_id),
        )
        return
    except DatabaseError as exc:
        logger.warning(
            "agent task progress persist failed %s error=%s",
            task_log_context(node_id=node_id, task_id=message.task_id),
            exc,
        )
        return
    try:
        from apps.protection.services.backup_orchestrator import (
            maybe_trigger_backup_advance,
            reattach_backup_node_task,
        )

        if task.status == NodeTask.Status.TIMEOUT:
            reattached = reattach_backup_node_task(node_task=task)
            if reattached is not None:
                task = reattached
        maybe_trigger_backup_advance(node_task=task)
        try:
            from apps.restore.services.restore_progress import maybe_trigger_restore_progress

            maybe_trigger_restore_progress(node_task=task)
        except Exception:
            logger.debug("restore progress after progress failed task_id=%s", message.task_id, exc_info=True)
    except Exception:
        logger.debug("backup advance after progress failed task_id=%s", message.task_id, exc_info=True)


def _handle_task_result(*, node_id: int, message: ParsedUplink) -> NodeTask:
    if not message.task_id:
        raise LookupError("task_id is required")
    previous_status = NodeTask.objects.filter(
        pk=message.task_id,
        node_id=node_id,
    ).values_list("status", flat=True).first()
    incoming_status = (message.status or "success").lower()
    is_retransmission = (
        previous_status == NodeTask.Status.SUCCESS
        and incoming_status in {"success", "succeeded", "ok"}
    ) or (
        previous_status == NodeTask.Status.FAILED
        and incoming_status not in {"success", "succeeded", "ok", "canceled", "cancelled", "running"}
    ) or (
        previous_status == NodeTask.Status.CANCELED
        and incoming_status in {"canceled", "cancelled"}
    )
    task = complete_task(
        task_id=message.task_id,
        node_id=node_id,
        status=message.status or "success",
        result=message.result or {},
        error=message.error,
    )
    if is_retransmission:
        TASK_RESULT_RETRANSMISSIONS.inc()
    logger.info(
        "agent task result committed %s status=%s",
        task_log_context(node_id=node_id, task_id=message.task_id, kind=task.kind),
        task.status,
    )
    return task


def trigger_task_result_followup(*, node_task_id) -> None:
    """Run domain follow-up after NodeTask commit/ACK; periodic jobs remain the fallback."""
    task = NodeTask.objects.filter(pk=node_task_id).first()
    if task is None:
        return
    try:
        from apps.node.services.internal.node_lifecycle import (
            queue_detached_remove_verification,
        )

        queue_detached_remove_verification(node_task=task)
    except Exception:
        logger.exception("lifecycle result follow-up queue failed task_id=%s", task.id)
    try:
        from apps.storage.services.internal.repository_agent_operation import (
            queue_repository_agent_result_followup,
        )

        queue_repository_agent_result_followup(node_task=task)
    except Exception:
        logger.exception("repository result follow-up queue failed task_id=%s", task.id)
    try:
        from apps.protection.services.backup_orchestrator import queue_backup_result_projection

        if queue_backup_result_projection(node_task=task):
            return
    except Exception:
        logger.exception("backup result projection queue failed task_id=%s", task.id)
    try:
        from apps.restore.services.restore_progress import maybe_trigger_restore_progress

        maybe_trigger_restore_progress(node_task=task)
    except Exception:
        logger.debug("restore progress after result failed task_id=%s", task.id, exc_info=True)
