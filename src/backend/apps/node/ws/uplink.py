"""
Agent WebSocket session and uplink for lifecycle and task frames.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import redis
from django.db import DatabaseError, transaction
from django.utils import timezone

from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.metrics import TASK_RESULT_DISPOSITIONS, TASK_RESULT_RETRANSMISSIONS
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

if TYPE_CHECKING:
    from redis import Redis


TaskResultDisposition = Literal[
    "accepted",
    "duplicate",
    "discarded_deleted_task",
    "discarded_invalid",
    "discarded_owner_mismatch",
    "discarded_stale_owner",
    "discarded_unknown",
]


@dataclass(frozen=True)
class TaskResultHandling:
    """Final handling decision for one durable Agent task result."""

    task_id: str
    disposition: TaskResultDisposition
    node_task: NodeTask | None = None
    # This is the Agent-facing protocol ACK.  Internal Redis stream entries
    # are acknowledged separately by the stream consumer.
    acknowledge_agent: bool = True


def on_agent_connected(*, node_id: int, session_id: str, client_ip: str | None = None) -> None:
    redis_client = redis_store.get_redis()
    route_recorded = redis_store.set_agent_location(
        agent_id=node_id,
        session_id=session_id,
        redis_client=redis_client,
    )
    if route_recorded:
        redis_store.touch_ws_instance_alive(redis_client=redis_client)
    observed_at = timezone.now()
    updates: dict = {
        "last_seen_at": observed_at,
    }
    if client_ip:
        updates["connection_ip_address"] = client_ip
    Node.objects.filter(pk=node_id).update(**updates)
    record_node_available(node_id=node_id, observed_at=observed_at)
    if route_recorded:
        _record_upgrade_session(
            node_id=node_id,
            session_id=session_id,
            redis_client=redis_client,
        )
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
    _schedule_lifecycle_advance(
        node_id=node_id,
        redis_client=redis_client if route_recorded else None,
    )


def on_agent_disconnected(*, node_id: int, session_id: str) -> None:
    redis_client = redis_store.get_redis()
    if not redis_store.clear_agent_location_if_session(
        agent_id=node_id,
        session_id=session_id,
        redis_client=redis_client,
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
    _schedule_lifecycle_advance(node_id=node_id, redis_client=redis_client)


def _schedule_lifecycle_advance(
    *,
    node_id: int,
    redis_client: Redis | None,
) -> None:
    if not redis_store.claim_lifecycle_advance_event(
        node_id=int(node_id),
        redis_client=redis_client,
    ):
        logger.debug(
            "Agent lifecycle wake-up coalesced or deferred node_id=%s",
            node_id,
        )
        return
    from apps.node.tasks.lifecycle import advance_node_lifecycle_for_node

    try:
        advance_node_lifecycle_for_node.apply_async(
            kwargs={"node_id": int(node_id)},
            expires=node_conf.LIFECYCLE_ADVANCE_EXPIRE_SECONDS,
        )
    except Exception:
        # Connection state is authoritative in Redis/PostgreSQL and the
        # periodic lifecycle sweep will retry after broker recovery.  A
        # disposable wake-up must never tear down a healthy WebSocket.
        logger.warning(
            "failed to enqueue Agent lifecycle wake-up node_id=%s",
            node_id,
            exc_info=True,
        )


def _record_upgrade_session(
    *,
    node_id: int,
    session_id: str,
    inventory: dict | None = None,
    redis_client=None,
) -> None:
    if redis_client is None:
        ownership = redis_store.is_agent_session_current(
            agent_id=node_id,
            session_id=session_id,
        )
    else:
        ownership = redis_store.is_agent_session_current(
            agent_id=node_id,
            session_id=session_id,
            redis_client=redis_client,
        )
    if ownership is not True:
        logger.debug(
            "stale Agent lifecycle session observation ignored node_id=%s session=%s",
            node_id,
            session_id,
        )
        return
    from apps.node.services.internal.node_lifecycle import (
        record_upgrade_session_observation,
    )

    try:
        changed = record_upgrade_session_observation(
            node_id=node_id,
            session_id=session_id,
            inventory=inventory,
        )
        if changed and inventory is not None:
            logger.debug(
                "post-upgrade inventory observed node_id=%s session=%s",
                node_id,
                session_id,
            )
    except DatabaseError:
        logger.warning(
            "post-upgrade session observation failed node_id=%s session=%s",
            node_id,
            session_id,
            exc_info=True,
        )


def handle_uplink(
    *,
    node_id: int,
    message: ParsedUplink,
    session_id: str | None = None,
) -> NodeTask | TaskResultHandling | None:
    if message.msg_type == WireType.HEARTBEAT:
        if session_id and redis_store.is_agent_session_current(
            agent_id=node_id,
            session_id=session_id,
        ) is False:
            logger.debug(
                "stale queued Agent heartbeat ignored node_id=%s session=%s",
                node_id,
                session_id,
            )
            return None
        _process_heartbeat_followup(
            node_id=node_id,
            inventory=message.heartbeat_payload,
            session_id=session_id,
        )
        return None

    if message.msg_type in (WireType.TASK_PROGRESS, WireType.TASK_ALIVE):
        if session_id and redis_store.is_agent_session_current(
            agent_id=node_id,
            session_id=session_id,
        ) is False:
            logger.debug(
                "stale Agent task progress ignored node_id=%s session=%s task_id=%s",
                node_id,
                session_id,
                message.task_id or "",
            )
            return None
        _handle_task_progress(node_id=node_id, message=message, session_id=session_id)
        return None

    if message.msg_type == WireType.TASK_ACCEPTED:
        if session_id and redis_store.is_agent_session_current(
            agent_id=node_id,
            session_id=session_id,
        ) is False:
            logger.debug(
                "stale Agent task acceptance ignored node_id=%s session=%s task_id=%s",
                node_id,
                session_id,
                message.task_id or "",
            )
            return None
        return accept_task(task_id=message.task_id or "", node_id=node_id)

    if message.msg_type == WireType.TASK_RESULT:
        if session_id and redis_store.is_agent_session_current(
            agent_id=node_id,
            session_id=session_id,
        ) is False:
            return _discard_task_result(
                node_id=node_id,
                task_id=message.task_id or "",
                disposition="discarded_stale_owner",
                acknowledge_agent=False,
            )
        return _handle_task_result_delivery(
            node_id=node_id, message=message, session_id=session_id
        )
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


def apply_heartbeat_inventory_snapshot(
    *, node_id: int, inventory: dict | None, session_id: str | None = None
) -> None:
    """Hot path: persist list/detail inventory fields without waiting for Celery ingest."""
    if not inventory:
        return
    ownership = (
        redis_store.is_agent_session_current(
            agent_id=node_id,
            session_id=session_id,
        )
        if session_id
        else True
    )
    observed_at = timezone.now()
    if ownership is False:
        # A superseded socket may still deliver a queued heartbeat after a
        # newer connection owns the route. Its inventory must not overwrite
        # the newer session's build evidence in PostgreSQL.
        logger.debug(
            "stale Agent heartbeat inventory ignored node_id=%s session=%s",
            node_id,
            session_id,
        )
        return
    if session_id and ownership is None:
        # Redis outage is not evidence that this socket is stale. Preserve
        # liveness, but do not persist session-sensitive inventory until route
        # ownership can be checked again.
        node = _persist_heartbeat_snapshot(
            node_id=node_id,
            inventory=None,
            observed_at=observed_at,
            merge_inventory=False,
        )
        if node is not None:
            record_node_available(node_id=node_id, observed_at=observed_at)
        return
    node = _persist_heartbeat_snapshot(
        node_id=node_id,
        inventory=inventory,
        observed_at=observed_at,
        merge_inventory=True,
    )
    if node is None:
        return
    record_node_available(node_id=node_id, observed_at=observed_at)
    if session_id:
        _record_upgrade_session(
            node_id=node_id,
            session_id=session_id,
            inventory=inventory,
        )
        _schedule_lifecycle_advance(
            node_id=node_id,
            redis_client=redis_store.get_redis(),
        )


def _should_process_full_inventory(*, node_id: int) -> bool:
    r = redis_store.get_redis()
    if r is None:
        return True
    key = _inventory_throttle_key(node_id=node_id)
    try:
        if r.exists(key):
            return False
        r.set(
            key,
            "1",
            ex=max(60, int(node_conf.HEARTBEAT_INVENTORY_MIN_INTERVAL_SECONDS)),
        )
        return True
    except redis.RedisError as exc:
        logger.warning(
            "heartbeat inventory throttle unavailable node_id=%s: %s", node_id, exc
        )
        return True


def _process_heartbeat_followup(
    *, node_id: int, inventory: dict | None = None, session_id: str | None = None
) -> None:
    # The WebSocket hot path renews the route with its authenticated session.
    # A delayed queue item must not refresh a superseded session's lease.
    redis_store.touch_ws_instance_alive()
    ownership = (
        redis_store.is_agent_session_current(
            agent_id=node_id,
            session_id=session_id,
        )
        if session_id
        else True
    )
    if ownership is False:
        logger.debug(
            "stale queued Agent heartbeat ignored node_id=%s session=%s",
            node_id,
            session_id,
        )
        return
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
        ownership is True
        and full_inventory
        and inventory
        and isinstance(inventory.get("metrics"), dict)
        and inventory["metrics"]
    ):
        from apps.monitor.services.internal.node_metrics import ingest_node_monitor_sample

        ingest_node_monitor_sample(node=node, sample=inventory["metrics"])

    if full_inventory and ownership is True:
        try:
            sync_agent_source_host_by_id(node_id=node_id)
        except Exception:
            logger.debug("agent source-host sync failed node_id=%s", node_id, exc_info=True)


def _handle_task_progress(
    *, node_id: int, message: ParsedUplink, session_id: str | None = None
) -> None:
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
    if session_id and task.kind == "agent.upgrade":
        _record_upgrade_session(node_id=node_id, session_id=session_id)
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


@transaction.atomic
def _handle_task_result(
    *,
    node_id: int,
    message: ParsedUplink,
    previous_status: str | None = None,
    session_id: str | None = None,
) -> NodeTask:
    if not message.task_id:
        raise LookupError("task_id is required")
    task = (
        NodeTask.objects.select_for_update()
        .filter(pk=message.task_id, node_id=node_id)
        .first()
    )
    if task is None:
        raise LookupError("task not found")
    if previous_status is None:
        previous_status = task.status
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
    incoming_result = dict(message.result or {})
    lifecycle_upgrade = (
        task.kind == "agent.upgrade"
        and task.correlation_type == node_conf.LIFECYCLE_CORRELATION_TYPE
    )
    if lifecycle_upgrade and str(incoming_status).lower() in {
        "success", "succeeded", "ok"
    }:
        incoming_result = {
            **dict(task.result or {}),
            **incoming_result,
            "host_upgrade_status": "success",
            "host_result_received_at": timezone.now().isoformat(),
        }
    elif lifecycle_upgrade and incoming_status not in {
        "running",
        "canceled",
        "cancelled",
    }:
        incoming_result = {
            **dict(task.result or {}),
            **incoming_result,
            "host_upgrade_status": "failed",
            "failure_code": "HOST_UPGRADE_FAILED",
            "host_error": message.error or incoming_status,
            "host_result_received_at": timezone.now().isoformat(),
        }
    persisted_status = message.status or "success"
    detached_result = dict(task.result or {})
    detached_mode = str(
        incoming_result.get("mode")
        or detached_result.get("mode")
        or (
            detached_result.get("last_progress", {}).get("mode")
            if isinstance(detached_result.get("last_progress"), dict)
            else ""
        )
        or ""
    ).strip()
    if (
        lifecycle_upgrade
        and task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}
        and incoming_status in {"success", "succeeded", "ok"}
        and detached_mode == "local_detached"
    ):
        # Detached host success is evidence, not final lifecycle success. The
        # coordinator must still validate the new session and inventory.
        persisted_status = "running"
    task = complete_task(
        task_id=message.task_id,
        node_id=node_id,
        status=persisted_status,
        result=incoming_result,
        error=message.error,
    )
    if (
        lifecycle_upgrade
        and task.status == NodeTask.Status.FAILED
        and not (task.result or {}).get("lifecycle_failure_audited")
    ):
        from apps.audit.constants import AuditResult
        from apps.audit.services.interface import write_audit_log

        sealed = dict(task.result or {})
        sealed["lifecycle_failure_audited"] = True
        task.result = sealed
        task.save(update_fields=["result", "updated_at"])
        node = task.node
        write_audit_log(
            organization=node.organization,
            action="node.lifecycle.upgrade.failed",
            target_type="node",
            target_id=str(node.id),
            resource_type="node",
            resource_id=str(node.id),
            resource_name=node.name,
            result=AuditResult.FAILURE,
            error_message=task.last_error,
            metadata={
                "kind": "upgrade",
                "task_id": str(task.id),
                "failure_code": sealed.get("failure_code") or "HOST_UPGRADE_FAILED",
            },
        )
    if session_id and lifecycle_upgrade:
        _record_upgrade_session(node_id=node_id, session_id=session_id)
    if is_retransmission:
        TASK_RESULT_RETRANSMISSIONS.inc()
    logger.info(
        "agent task result committed %s status=%s",
        task_log_context(node_id=node_id, task_id=message.task_id, kind=task.kind),
        task.status,
    )
    return task


@transaction.atomic
def _handle_task_result_delivery(
    *,
    node_id: int,
    message: ParsedUplink,
    session_id: str | None = None,
) -> TaskResultHandling:
    """Apply or permanently discard a task result before acknowledging it."""

    if not message.task_id:
        raise LookupError("task_id is required")
    try:
        uuid.UUID(str(message.task_id))
    except (AttributeError, TypeError, ValueError):
        return _discard_task_result(
            node_id=node_id,
            task_id=str(message.task_id),
            disposition="discarded_invalid",
        )

    task = (
        # Lock only the task row.  Node lifecycle removal locks the Node row
        # before touching its dependent records; locking both tables here
        # would invert that order and create an avoidable deadlock.
        NodeTask.all_objects.select_for_update(of=("self",))
        .select_related("node")
        .filter(pk=message.task_id)
        .first()
    )
    if task is None:
        return _discard_task_result(
            node_id=node_id,
            task_id=message.task_id,
            disposition="discarded_unknown",
        )

    owner_node_id = int(task.node_id)
    if task.is_deleted:
        return _discard_task_result(
            node_id=node_id,
            task_id=message.task_id,
            disposition="discarded_deleted_task",
            owner_node_id=owner_node_id,
        )

    if task.node.is_deleted:
        return _discard_task_result(
            node_id=node_id,
            task_id=message.task_id,
            disposition="discarded_stale_owner",
            owner_node_id=owner_node_id,
        )

    if owner_node_id != node_id:
        # The WebSocket is authenticated for node_id, so this result can
        # never be applied to the task's immutable owner.  Do not ACK it;
        # retaining it exposes the routing fault to the Agent retry path.
        return _discard_task_result(
            node_id=node_id,
            task_id=message.task_id,
            disposition="discarded_owner_mismatch",
            owner_node_id=owner_node_id,
            acknowledge_agent=False,
        )

    task = _handle_task_result(
        node_id=node_id,
        message=message,
        previous_status=task.status,
        session_id=session_id,
    )
    disposition = (
        "duplicate"
        if bool(getattr(task, "_result_retransmission_unchanged", False))
        else "accepted"
    )
    TASK_RESULT_DISPOSITIONS.labels(disposition=disposition).inc()
    return TaskResultHandling(
        task_id=message.task_id,
        disposition=disposition,
        node_task=task,
    )


def _discard_task_result(
    *,
    node_id: int,
    task_id: str,
    disposition: TaskResultDisposition,
    owner_node_id: int | None = None,
    acknowledge_agent: bool = True,
) -> TaskResultHandling:
    """Record a permanent rejection without creating or updating a task."""

    TASK_RESULT_DISPOSITIONS.labels(disposition=disposition).inc()
    log = logger.warning if disposition == "discarded_owner_mismatch" else logger.info
    log(
        "agent task result discarded node_id=%s task_id=%s owner_node_id=%s disposition=%s",
        node_id,
        task_id,
        owner_node_id if owner_node_id is not None else "-",
        disposition,
    )
    return TaskResultHandling(
        task_id=task_id,
        disposition=disposition,
        acknowledge_agent=acknowledge_agent,
    )


def project_identical_task_result_recovery(*, node_task: NodeTask) -> None:
    """Apply only idempotent health recovery for an unchanged terminal replay."""

    from apps.storage.services.internal.repository_health import (
        project_repository_health_from_agent_result,
    )

    project_repository_health_from_agent_result(
        node_task=node_task,
        allow_failure=False,
    )
    from apps.source.tasks.connection_probe import project_source_connection_probe

    project_source_connection_probe(node_task=node_task)


def trigger_task_result_followup(*, node_task_id) -> None:
    """Run domain follow-up after NodeTask commit/ACK; periodic jobs remain the fallback."""
    task = NodeTask.objects.filter(pk=node_task_id).first()
    if task is None:
        return
    try:
        from apps.storage.services.internal.repository_health import (
            project_repository_health_from_agent_result,
        )

        project_repository_health_from_agent_result(node_task=task)
    except Exception:
        logger.exception("repository health result projection failed task_id=%s", task.id)
    try:
        from apps.source.tasks.connection_probe import (
            project_source_connection_probe,
        )

        project_source_connection_probe(node_task=task)
    except Exception:
        logger.exception("source connection result projection failed task_id=%s", task.id)
    try:
        from apps.protection.services.snapshot_delete_execution import (
            queue_snapshot_delete_result_followup,
        )

        queue_snapshot_delete_result_followup(node_task=task)
    except Exception:
        logger.exception("snapshot delete result follow-up failed task_id=%s", task.id)
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
        if task.correlation_type == "protection.backup_config":
            from apps.protection.services.backup_config_provision import (
                queue_backup_config_provision_task,
            )

            if task.parent_task_id:
                queue_backup_config_provision_task(task_id=task.parent_task_id)
    except Exception:
        logger.exception(
            "backup config provision result follow-up queue failed task_id=%s",
            task.id,
        )
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
