"""
NodeTask write path: create, deliver, progress, complete, and watchdog sweep.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import redis
from channels.exceptions import ChannelFull, InvalidChannelLayerError, MessageTooLarge
from django.db import transaction
from django.db.models import F, Q, QuerySet
from django.utils import timezone

from apps.iam.models import Organization
from apps.node import conf as node_conf
from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.node.services.internal import redis_store
from apps.node.services.internal.agent_log import task_log_context
from apps.storage.crypto import decrypt_text, encrypt_text

if TYPE_CHECKING:
    from apps.task.models import Task

logger = logging.getLogger(__name__)

_ACTIVE_STATUSES = (NodeTask.Status.PENDING, NodeTask.Status.RUNNING)
_TERMINAL_STATUSES = frozenset(
    {
        NodeTask.Status.SUCCESS,
        NodeTask.Status.FAILED,
        NodeTask.Status.TIMEOUT,
        NodeTask.Status.CANCELED,
    },
)
_TASK_COMMAND_ACK_CAPABILITY = "task_command_ack_v1"
_DELIVERY_SECRET_KEY = "_delivery_secret"
_DELIVERY_PROTOCOL_KEY = "_delivery_protocol"
_DELIVERY_PROTOCOL_ACK = "command_ack_v1"
_DELIVERY_PROTOCOL_LEGACY = "legacy"
_DOWNLINK_ERRORS = (
    RuntimeError,
    OSError,
    redis.RedisError,
    ChannelFull,
    InvalidChannelLayerError,
    MessageTooLarge,
)
_ACK_DURABLE_TASKS = frozenset(
    {
        ("protection.backup", "backup.run"),
        ("protection.backup", "backup.snapshot.create"),
        ("protection.backup.policy_prepare", "repository.policy.apply"),
        ("protection.snapshot_delete", "snapshot.delete"),
        ("protection.backup_config_reset", "snapshot.delete"),
        ("source.connection_probe", "nas.test"),
        ("storage.repository_health", "repo.status"),
        ("repository_create", "repo.initialize"),
        ("protection.backup_config", "repo.initialize"),
    }
)
_LIFECYCLE_PRE_DISPATCH_KINDS = frozenset({"agent.upgrade", "agent.uninstall"})


@dataclass(frozen=True)
class DispatchResult:
    task: NodeTask


class _RouteState(StrEnum):
    ONLINE = "online"
    RECONNECTING = "reconnecting"
    OFFLINE = "offline"


def protect_task_delivery_payload(
    *,
    delivery_payload: dict[str, Any],
    persisted_payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist a redeliverable payload without storing its plaintext form."""

    encoded = json.dumps(
        delivery_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    protected = dict(persisted_payload)
    protected[_DELIVERY_SECRET_KEY] = {
        "alg": "fernet-json-v1",
        "ciphertext": encrypt_text(encoded),
    }
    return protected


def _resolve_task_delivery_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    envelope = payload.get(_DELIVERY_SECRET_KEY)
    if envelope is None:
        return None
    if not isinstance(envelope, dict) or envelope.get("alg") != "fernet-json-v1":
        raise RuntimeError("agent task delivery envelope is invalid")
    try:
        decoded = json.loads(decrypt_text(str(envelope.get("ciphertext") or "")))
    except Exception as exc:
        raise RuntimeError("agent task delivery envelope cannot be decrypted") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError("agent task delivery envelope is invalid")
    return decoded


def _watchdog_deadline(*, from_time: datetime | None = None) -> datetime:
    base = from_time or timezone.now()
    return base + timezone.timedelta(seconds=node_conf.TASK_WATCHDOG_SECONDS)


def _protection_backup_watchdog_deadline(
    *, from_time: datetime | None = None
) -> datetime:
    from apps.protection import conf as protection_conf

    base = from_time or timezone.now()
    return base + timezone.timedelta(
        seconds=protection_conf.PROTECTION_BACKUP_ACTIVITY_LEASE_SECONDS
    )


def _lifecycle_detached_watchdog_deadline(
    *, from_time: datetime | None = None
) -> datetime:
    base = from_time or timezone.now()
    return base + timezone.timedelta(
        seconds=node_conf.LIFECYCLE_DETACHED_TIMEOUT_SECONDS
    )


def _is_protection_backup_task(task: NodeTask) -> bool:
    from apps.protection import conf as protection_conf

    return (
        task.correlation_type == protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE
        and task.kind in protection_conf.PROTECTION_BACKUP_NODE_TASK_KINDS
    )


def _is_source_nas_probe(*, correlation_type: str, kind: str) -> bool:
    return correlation_type == "source.connection_probe" and kind == "nas.test"


def _is_repository_initialize_task(*, correlation_type: str, kind: str) -> bool:
    return (
        correlation_type in {"repository_create", "protection.backup_config"}
        and kind == "repo.initialize"
    )


def _source_nas_probe_deadline(*, accepted_at: datetime) -> datetime:
    return accepted_at + timezone.timedelta(
        seconds=max(1, node_conf.SOURCE_NAS_PROBE_EXECUTION_TIMEOUT_SECONDS)
    )


def _source_nas_probe_started_at(*, task: NodeTask, fallback: datetime) -> datetime:
    """Return the probe's immutable execution start for old and new Agents."""
    return (
        task.accepted_at
        or task.dispatched_at
        or task.last_progress_at
        or task.created_at
        or fallback
    )


def _node_capabilities(node: Node) -> set[str]:
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    values = inventory.get("capabilities", metadata.get("capabilities", []))
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(value or "").strip() for value in values if str(value or "").strip()}


def task_uses_command_ack(task: NodeTask) -> bool:
    """Return the immutable delivery protocol selected for this task.

    Agent capabilities may disappear temporarily during reconnects or change
    after an upgrade.  Once a command has been sent with durable ACK semantics,
    its PostgreSQL record must retain those semantics for reconciliation.
    """
    if (task.correlation_type, task.kind) not in _ACK_DURABLE_TASKS:
        return False
    result = task.result if isinstance(task.result, dict) else {}
    selected = str(result.get(_DELIVERY_PROTOCOL_KEY) or "")
    if selected == _DELIVERY_PROTOCOL_ACK:
        return True
    if selected == _DELIVERY_PROTOCOL_LEGACY:
        return False
    # Backward compatibility for ACK commands dispatched before protocol
    # selection was persisted. Legacy commands transition to RUNNING in the
    # same write as their first delivery, so a dispatched PENDING task is ACK.
    if (
        task.status == NodeTask.Status.PENDING
        and task.accepted_at is None
        and task.delivery_attempt_count > 0
    ):
        return True
    return _TASK_COMMAND_ACK_CAPABILITY in _node_capabilities(task.node)


def _persist_delivery_protocol(*, task: NodeTask) -> bool:
    """Select the delivery protocol once and persist it without a migration."""
    if (task.correlation_type, task.kind) not in _ACK_DURABLE_TASKS:
        return False
    with transaction.atomic():
        locked = (
            NodeTask.objects.select_for_update().select_related("node").get(pk=task.pk)
        )
        task.status = locked.status
        task.accepted_at = locked.accepted_at
        if locked.status != NodeTask.Status.PENDING or locked.accepted_at is not None:
            task.result = locked.result
            return False
        result = dict(locked.result or {})
        selected = str(result.get(_DELIVERY_PROTOCOL_KEY) or "")
        if selected not in {_DELIVERY_PROTOCOL_ACK, _DELIVERY_PROTOCOL_LEGACY}:
            uses_ack = (
                locked.delivery_attempt_count > 0
                or _TASK_COMMAND_ACK_CAPABILITY in _node_capabilities(locked.node)
            )
            selected = _DELIVERY_PROTOCOL_ACK if uses_ack else _DELIVERY_PROTOCOL_LEGACY
            result[_DELIVERY_PROTOCOL_KEY] = selected
            locked.result = result
            if uses_ack:
                locked.watchdog_deadline_at = timezone.now() + timezone.timedelta(
                    seconds=max(1, node_conf.TASK_COMMAND_ACK_MAX_AGE_SECONDS)
                )
                update_fields = ["result", "watchdog_deadline_at", "updated_at"]
            else:
                update_fields = ["result", "updated_at"]
            locked.save(update_fields=update_fields)
        task.result = locked.result
        task.watchdog_deadline_at = locked.watchdog_deadline_at
        task.delivery_attempt_count = locked.delivery_attempt_count
        return selected == _DELIVERY_PROTOCOL_ACK


def _substantive_backup_progress(progress: dict[str, Any] | None) -> bool:
    if not isinstance(progress, dict) or not progress:
        return False
    for key in ("hashed_bytes", "uploaded_bytes", "kopia_percent", "percent"):
        if progress.get(key) not in (None, ""):
            return True
    phase = (
        str(progress.get("kopia_phase") or progress.get("phase") or "").strip().lower()
    )
    return phase in {
        "hashing",
        "uploading",
        "restoring",
        "snapshot_created",
        "restore_completed",
        "repository_ready",
        "snapshot_start",
        "kopia_snapshot",
        "kopia_transfer",
    }


def _initial_watchdog_deadline(
    *,
    correlation_type: str,
    from_time: datetime | None = None,
    kind: str = "",
) -> datetime:
    if _is_repository_initialize_task(
        correlation_type=correlation_type,
        kind=kind,
    ):
        base = from_time or timezone.now()
        return base + timezone.timedelta(
            seconds=max(1, node_conf.REPOSITORY_INITIALIZE_WATCHDOG_SECONDS)
        )
    if _is_source_nas_probe(correlation_type=correlation_type, kind=kind):
        return _source_nas_probe_deadline(accepted_at=from_time or timezone.now())
    if correlation_type in {
        "source.connection_probe",
        "storage.repository_health",
    }:
        base = from_time or timezone.now()
        return base + timezone.timedelta(
            seconds=max(1, node_conf.AUTOMATIC_PROBE_WATCHDOG_SECONDS)
        )
    if correlation_type in {
        "protection.snapshot_delete",
        "protection.backup_config_reset",
    } and kind == "snapshot.delete":
        base = from_time or timezone.now()
        return base + timezone.timedelta(
            seconds=max(1, node_conf.SNAPSHOT_DELETE_WATCHDOG_SECONDS)
        )
    if correlation_type == node_conf.LIFECYCLE_CORRELATION_TYPE:
        return _lifecycle_detached_watchdog_deadline(from_time=from_time)
    from apps.protection import conf as protection_conf

    if (
        correlation_type == protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE
        and kind in protection_conf.PROTECTION_BACKUP_NODE_TASK_KINDS
    ):
        return _protection_backup_watchdog_deadline(from_time=from_time)
    if (
        correlation_type
        == protection_conf.PROTECTION_BACKUP_POLICY_PREPARE_CORRELATION_TYPE
        and kind == "repository.policy.apply"
    ):
        return _protection_backup_watchdog_deadline(from_time=from_time)
    return _watchdog_deadline(from_time=from_time)


def _is_lifecycle_correlated_task(task: NodeTask) -> bool:
    return task.correlation_type == node_conf.LIFECYCLE_CORRELATION_TYPE


def _task_has_detached_marker(task: NodeTask) -> bool:
    if not _is_lifecycle_correlated_task(task):
        return False
    result = task.result if isinstance(task.result, dict) else {}
    if str(result.get("mode") or "").strip() == "local_detached":
        return True
    progress = result.get("last_progress")
    if isinstance(progress, dict):
        return str(progress.get("mode") or "").strip() == "local_detached"
    return False


def _should_apply_progress_update(
    *,
    task: NodeTask,
    incoming: dict[str, Any],
    existing: dict[str, Any] | None,
) -> bool:
    """Ignore generic agent heartbeats once substantive Kopia progress exists."""
    if not _is_protection_backup_task(task):
        return True
    if _substantive_backup_progress(incoming):
        return True
    if not isinstance(existing, dict) or not _substantive_backup_progress(existing):
        return True
    return False


def _merge_progress_into_result(
    *,
    task: NodeTask,
    progress: dict[str, Any],
    merged: dict[str, Any],
    now: datetime,
) -> None:
    existing = merged.get("last_progress")
    if not _should_apply_progress_update(
        task=task,
        incoming=progress,
        existing=existing if isinstance(existing, dict) else None,
    ):
        return
    merged["last_progress"] = progress
    mode = str(progress.get("mode") or "").strip()
    if mode != "local_detached":
        return
    merged["mode"] = mode
    target = str(progress.get("target_version") or "").strip()
    if target:
        merged["target_version"] = target
    if not merged.get("detached_at"):
        merged["detached_at"] = now.isoformat()


def _apply_detached_running_state(
    *, task: NodeTask, merged: dict[str, Any], now: datetime
) -> list[str]:
    extra_fields: list[str] = []
    if str(merged.get("mode") or "").strip() != "local_detached":
        return extra_fields
    if not merged.get("detached_at"):
        merged["detached_at"] = now.isoformat()
        extra_fields.append("result")
    task.watchdog_deadline_at = _lifecycle_detached_watchdog_deadline(from_time=now)
    task.last_progress_at = now
    extra_fields.extend(["watchdog_deadline_at", "last_progress_at"])
    return extra_fields


def _send_task_command(*, task: NodeTask) -> None:
    from apps.node.ws.downlink import send_task_command

    send_task_command(task=task)


def _node_ws_routable(*, node_id: int) -> bool:
    ws_instance = redis_store.get_agent_location(agent_id=node_id)
    if not ws_instance:
        return False
    client = redis_store.get_redis()
    if client is None:
        return True
    return bool(client.exists(redis_store.ws_alive_key(ws_instance)))


def _last_seen_within_task_route_grace(node: Node) -> bool:
    if not node.last_seen_at:
        return False
    grace = timezone.timedelta(
        seconds=max(0, int(node_conf.NODE_RECONNECT_GRACE_SECONDS))
    )
    return timezone.now() - node.last_seen_at < grace


def _task_within_route_grace(task: NodeTask) -> bool:
    grace = timezone.timedelta(
        seconds=max(0, int(node_conf.TASK_ROUTE_RECONNECT_GRACE_SECONDS))
    )
    return timezone.now() - task.created_at < grace


def _is_pre_dispatch_lifecycle_task(task: NodeTask) -> bool:
    """Return whether a lifecycle command is still safe to retry.

    Before the command is sent, retrying after a short WebSocket flap cannot
    duplicate an Agent operation.  Once delivery starts, detached upgrade and
    uninstall commands must retain their existing fail-closed semantics.
    """
    return (
        task.correlation_type == node_conf.LIFECYCLE_CORRELATION_TYPE
        and task.kind in _LIFECYCLE_PRE_DISPATCH_KINDS
        and task.dispatched_at is None
        and task.accepted_at is None
        and task.delivery_attempt_count == 0
    )


def _node_route_state(*, task: NodeTask) -> _RouteState:
    node = task.node
    if node.role not in (NodeRole.AGENT, NodeRole.PROXY):
        return _RouteState.ONLINE
    if redis_store.ws_recovery_hold_active():
        return _RouteState.RECONNECTING
    if _node_ws_routable(node_id=node.id):
        return _RouteState.ONLINE
    # A lifecycle command has not reached the Agent yet.  Keep it pending for
    # the existing 30-second route grace even if the node status projection has
    # already crossed from Online to Offline during a brief reconnect flap.
    if _is_pre_dispatch_lifecycle_task(task) and _task_within_route_grace(task):
        return _RouteState.RECONNECTING
    if (
        node.availability == Node.Availability.ONLINE
        and _last_seen_within_task_route_grace(node)
        and _task_within_route_grace(task)
    ):
        return _RouteState.RECONNECTING
    return _RouteState.OFFLINE


def _sync_task_info(task: NodeTask) -> None:
    try:
        redis_store.set_task_info(
            task_id=str(task.id),
            data={
                "task_id": str(task.id),
                "status": task.status,
                "node_id": task.node_id,
                "kind": task.kind,
                "correlation_type": task.correlation_type,
                "correlation_id": task.correlation_id,
                "last_progress_at": (
                    task.last_progress_at.isoformat() if task.last_progress_at else None
                ),
                "accepted_at": (
                    task.accepted_at.isoformat() if task.accepted_at else None
                ),
                "last_delivery_at": (
                    task.last_delivery_at.isoformat() if task.last_delivery_at else None
                ),
                "delivery_attempt_count": task.delivery_attempt_count,
                "watchdog_deadline_at": task.watchdog_deadline_at.isoformat(),
                "result": task.result,
                "last_error": task.last_error,
            },
        )
    finally:
        project_node_lifecycle_task(node_task=task)


def project_node_lifecycle_task(*, node_task: NodeTask) -> None:
    """Project lifecycle task progress into the persisted Node state machine."""
    payload = node_task.payload if isinstance(node_task.payload, dict) else {}
    if node_task.correlation_type != node_conf.LIFECYCLE_CORRELATION_TYPE:
        return
    if node_task.kind == "agent.upgrade":
        status = (
            Node.Status.UPGRADING
            if node_task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}
            else Node.Status.ACTIVE
            if node_task.status == NodeTask.Status.SUCCESS
            else Node.Status.UPGRADE_FAILED
        )
        updated = (
            Node.objects.filter(pk=node_task.node_id)
            .exclude(status=status)
            .update(status=status)
        )
        if updated:
            _sync_agent_source_pipeline_status(node_id=node_task.node_id)
        return
    if node_task.kind != "agent.uninstall":
        return
    status = (
        Node.Status.REMOVING
        if node_task.status in {NodeTask.Status.PENDING, NodeTask.Status.RUNNING}
        else Node.Status.CLEANING_UP
        if node_task.status == NodeTask.Status.SUCCESS
        else Node.Status.DEREGISTRATION_FAILED
    )
    updated = (
        Node.objects.filter(pk=node_task.node_id)
        .exclude(status=status)
        .update(status=status)
    )
    if updated:
        _sync_agent_source_pipeline_status(node_id=node_task.node_id)
    if payload.get("source_unregister_task_id"):
        return
    try:
        from apps.node.services.internal.node_lifecycle_task import (
            sync_node_remove_operation_task,
        )

        sync_node_remove_operation_task(node_task=node_task)
    except Exception:
        logger.exception(
            "failed to project node lifecycle task node_task_id=%s",
            node_task.pk,
        )


def _sync_agent_source_pipeline_status(*, node_id: int) -> None:
    """Refresh the Backup Wizard read model after a Node lifecycle transition."""
    node = Node.objects.filter(
        pk=node_id, role=NodeRole.AGENT, is_deleted=False
    ).first()
    if node is None:
        return
    try:
        from apps.source.services.internal.source_pipeline import (
            sync_pipeline_projection,
        )

        sync_pipeline_projection(
            organization_id=node.organization_id,
            source_kind="agent",
            ref_id=node.id,
        )
    except Exception:
        logger.warning(
            "agent source pipeline status projection failed node_id=%s",
            node_id,
            exc_info=True,
        )


def _terminal_stream_message(task: NodeTask) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "status": task.status,
        "result": task.result,
        "error": task.last_error,
    }


def _schedule_agent_task_redelivery(*, task: NodeTask) -> None:
    from apps.node.tasks.node_task import redeliver_agent_task

    redeliver_agent_task.apply_async(
        kwargs={"task_id": str(task.id)},
        countdown=1,
    )


def _delivery_diagnostic_error_code(reason: str) -> str:
    normalized_reason = reason.lower()
    if "reconnecting" in normalized_reason:
        return "AGENT_CONNECTION_UNSTABLE"
    if "not routable" in normalized_reason or (
        "agent" in normalized_reason and "unavailable" in normalized_reason
    ):
        return "AGENT_UNAVAILABLE"
    return "AGENT_DELIVERY_FAILED"


def _without_delivery_runtime_state(result: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(result or {})
    if merged.get("diagnostic_error_code") in {
        "AGENT_CONNECTION_UNSTABLE",
        "AGENT_UNAVAILABLE",
        "AGENT_DELIVERY_FAILED",
    }:
        merged.pop("diagnostic_error_code", None)
    merged.pop(_DELIVERY_PROTOCOL_KEY, None)
    return merged


def _mark_task_reconnecting(*, task: NodeTask) -> NodeTask:
    task.refresh_from_db()
    if task.status != NodeTask.Status.PENDING:
        _sync_task_info(task)
        return task
    result = dict(task.result or {})
    result["diagnostic_error_code"] = "AGENT_CONNECTION_UNSTABLE"
    updated = NodeTask.objects.filter(
        pk=task.pk, status=NodeTask.Status.PENDING
    ).update(
        last_error="agent websocket is reconnecting",
        result=result,
        updated_at=timezone.now(),
    )
    task.refresh_from_db()
    if updated == 0 or task.status != NodeTask.Status.PENDING:
        _sync_task_info(task)
        return task
    _sync_task_info(task)
    # Durable ACK commands are retried by the coalesced PostgreSQL
    # reconciliation sweep. Scheduling a one-second Celery chain as well would
    # amplify a flapping Agent into avoidable broker traffic. Legacy commands
    # still need the one-shot chain because they are not reconciliation
    # candidates.
    if not task_uses_command_ack(task):
        try:
            _schedule_agent_task_redelivery(task=task)
        except Exception:
            logger.warning(
                "failed to schedule agent task redelivery task_id=%s",
                task.id,
                exc_info=True,
            )
    logger.info(
        "agent task delivery delayed while websocket reconnects task_id=%s node_id=%s kind=%s",
        task.id,
        task.node_id,
        task.kind,
    )
    return task


def _fail_task_delivery(*, task: NodeTask, reason: str) -> NodeTask:
    result = _without_delivery_runtime_state(task.result)
    result["diagnostic_error_code"] = _delivery_diagnostic_error_code(reason)
    updated = NodeTask.objects.filter(
        pk=task.pk,
        status=NodeTask.Status.PENDING,
        accepted_at__isnull=True,
    ).update(
        status=NodeTask.Status.FAILED,
        last_error=reason[:2000],
        result=result,
        updated_at=timezone.now(),
    )
    task.refresh_from_db()
    if updated == 0:
        # A transport error can be ambiguous: the command may have reached a
        # legacy Agent and completed before Channels reported the failure.
        # Preserve the newer PostgreSQL state and its existing projection.
        return task
    _sync_task_info(task)
    redis_store.push_task_stream(
        task_id=str(task.id),
        message=_terminal_stream_message(task),
    )
    if task.correlation_type in {
        "source.connection_probe",
        "storage.repository_health",
        "protection.snapshot_delete",
        "protection.backup_config_reset",
    }:
        try:
            _project_terminal_node_task(task=task)
        except Exception:
            logger.exception(
                "agent task delivery failure projection failed task_id=%s kind=%s",
                task.id,
                task.kind,
            )
    return task


def create_agent_task(
    *,
    org: Organization,
    node: Node,
    kind: str,
    payload: dict | None = None,
    correlation_type: str = "",
    correlation_id: str = "",
    requesting_organization_id: int | None = None,
    parent_task: Task | None = None,
) -> NodeTask:
    if node.organization_id != org.id:
        raise ValueError("node/org mismatch")

    now = timezone.now()
    task = NodeTask.objects.create(
        organization=org,
        requesting_organization_id=requesting_organization_id or org.id,
        node=node,
        correlation_type=correlation_type or "",
        correlation_id=str(correlation_id or ""),
        parent_task=parent_task,
        kind=kind,
        payload=payload or {},
        status=NodeTask.Status.PENDING,
        watchdog_deadline_at=_initial_watchdog_deadline(
            correlation_type=correlation_type or "",
            from_time=now,
            kind=kind,
        ),
    )
    logger.info(
        "agent task created %s",
        task_log_context(
            node_id=node.id,
            task_id=str(task.id),
            kind=kind,
            correlation_type=correlation_type,
            correlation_id=correlation_id,
        ),
    )
    project_node_lifecycle_task(node_task=task)
    return task


def deliver_agent_task(
    *,
    task: NodeTask,
    delivery_payload: dict | None = None,
    allow_ack_redelivery: bool = False,
) -> NodeTask:
    """Send ``task.command`` while preserving the selected delivery protocol.

    ACK-capable backup tasks remain pending until accepted. Their retries are
    rejected by default so only the bounded PostgreSQL reconciliation sweep
    can opt into redelivery.
    """
    if task.status != NodeTask.Status.PENDING or task.accepted_at is not None:
        return task
    ctx = task_log_context(
        node_id=task.node_id,
        task_id=str(task.id),
        kind=task.kind,
        correlation_type=task.correlation_type,
        correlation_id=task.correlation_id,
    )
    uses_ack = _persist_delivery_protocol(task=task)
    if task.status != NodeTask.Status.PENDING or task.accepted_at is not None:
        return task
    if uses_ack and task.delivery_attempt_count > 0 and not allow_ack_redelivery:
        # ACK retries have one owner: the bounded PostgreSQL reconciliation
        # sweep. Backup orchestration may observe PENDING repeatedly, but must
        # not turn those observations into an unbounded delivery loop.
        return task
    try:
        ephemeral_delivery_payload = delivery_payload is not None
        if delivery_payload is None:
            delivery_payload = _resolve_task_delivery_payload(task.payload)
        route_state = _node_route_state(task=task)
        if route_state == _RouteState.RECONNECTING:
            if ephemeral_delivery_payload:
                return _fail_task_delivery(
                    task=task,
                    reason="agent websocket is reconnecting; retry the credential-bearing task",
                )
            return _mark_task_reconnecting(task=task)
        if route_state == _RouteState.OFFLINE:
            redis_store.clear_agent_location(agent_id=task.node_id)
            raise RuntimeError("agent websocket is not routable")
        logger.info("agent task dispatching %s", ctx)
        dispatched_at = timezone.now()
        if uses_ack:
            delivery_updates: dict[str, Any] = {
                "last_delivery_at": dispatched_at,
                "delivery_attempt_count": F("delivery_attempt_count") + 1,
                "last_error": "",
            }
            if task.dispatched_at is None:
                delivery_updates["dispatched_at"] = dispatched_at
            delivery_claimed = NodeTask.objects.filter(
                pk=task.pk,
                status=NodeTask.Status.PENDING,
                accepted_at__isnull=True,
                delivery_attempt_count=task.delivery_attempt_count,
            ).update(**delivery_updates)
            if delivery_claimed == 0:
                # Another dispatcher claimed this attempt, or the Agent
                # accepted/completed it after protocol selection. Preserve the
                # newer PostgreSQL state and do not emit a duplicate command.
                task.refresh_from_db()
                return task
        if delivery_payload is None:
            _send_task_command(task=task)
        else:
            persisted_payload = task.payload
            task.payload = delivery_payload
            try:
                _send_task_command(task=task)
            finally:
                task.payload = persisted_payload
        if not uses_ack:
            completed_delivery_result = _without_delivery_runtime_state(task.result)
            NodeTask.objects.filter(
                pk=task.pk,
                status=NodeTask.Status.PENDING,
                accepted_at__isnull=True,
            ).update(
                status=NodeTask.Status.RUNNING,
                dispatched_at=dispatched_at,
                last_delivery_at=dispatched_at,
                delivery_attempt_count=F("delivery_attempt_count") + 1,
                last_progress_at=dispatched_at,
                watchdog_deadline_at=_initial_watchdog_deadline(
                    correlation_type=task.correlation_type,
                    from_time=dispatched_at,
                    kind=task.kind,
                ),
                result=completed_delivery_result,
            )
        task.refresh_from_db()
        _sync_task_info(task)
        logger.info("agent task dispatched %s status=%s", ctx, task.status)
    except _DOWNLINK_ERRORS as exc:
        logger.warning(
            "agent task dispatch failed %s error=%s",
            ctx,
            str(exc)[:500],
        )
        if uses_ack:
            result = dict(task.result or {})
            result["diagnostic_error_code"] = _delivery_diagnostic_error_code(str(exc))
            NodeTask.objects.filter(pk=task.pk, status=NodeTask.Status.PENDING).update(
                last_error=str(exc)[:2000],
                result=result,
                updated_at=timezone.now(),
            )
            task.refresh_from_db()
            _sync_task_info(task)
        else:
            task = _fail_task_delivery(task=task, reason=str(exc))
    return task


@transaction.atomic
def accept_task(*, task_id: uuid.UUID | str, node_id: int) -> NodeTask:
    """Persist Agent's durable command acceptance without reviving terminal work."""
    task = (
        NodeTask.objects.select_for_update().filter(pk=task_id, node_id=node_id).first()
    )
    if task is None:
        raise LookupError("task not found")
    if task.status in _TERMINAL_STATUSES:
        return task
    now = timezone.now()
    task.status = NodeTask.Status.RUNNING
    task.accepted_at = task.accepted_at or now
    task.last_progress_at = task.last_progress_at or now
    task.watchdog_deadline_at = _initial_watchdog_deadline(
        correlation_type=task.correlation_type,
        from_time=now,
        kind=task.kind,
    )
    task.last_error = ""
    task.result = _without_delivery_runtime_state(task.result)
    task.save(
        update_fields=[
            "status",
            "accepted_at",
            "last_progress_at",
            "watchdog_deadline_at",
            "last_error",
            "result",
            "updated_at",
        ]
    )
    _sync_task_info(task)
    redis_store.push_task_stream(
        task_id=str(task.id),
        message={"task_id": str(task.id), "status": task.status, "accepted": True},
    )
    return task


def redeliver_pending_agent_task(*, task_id: uuid.UUID | str) -> NodeTask | None:
    task = NodeTask.objects.select_related("node").filter(pk=task_id).first()
    if task is None:
        return None
    if task.status != NodeTask.Status.PENDING:
        return task
    delivered = deliver_agent_task(task=task)
    if delivered.status == NodeTask.Status.RUNNING:
        logger.info(
            "agent task redelivery succeeded task_id=%s node_id=%s kind=%s",
            delivered.id,
            delivered.node_id,
            delivered.kind,
        )
    elif delivered.status == NodeTask.Status.FAILED:
        logger.info(
            "agent task redelivery failed task_id=%s node_id=%s kind=%s error=%s",
            delivered.id,
            delivered.node_id,
            delivered.kind,
            delivered.last_error,
        )
    return delivered


def _timeout_unaccepted_task(
    *,
    task_id: uuid.UUID | str,
    error_code: str = "AGENT_ACK_TIMEOUT",
    message: str = "Agent did not durably accept task.command",
) -> bool:
    with transaction.atomic():
        task = (
            NodeTask.objects.select_for_update()
            .filter(
                pk=task_id, status=NodeTask.Status.PENDING, accepted_at__isnull=True
            )
            .first()
        )
        if task is None:
            return False
        task.status = NodeTask.Status.TIMEOUT
        task.last_error = f"{error_code}: {message}"
        result = dict(task.result or {})
        result.pop(_DELIVERY_PROTOCOL_KEY, None)
        result["diagnostic_error_code"] = error_code
        result["delivery_timeout_sealed"] = True
        result["delivery_attempt_count"] = task.delivery_attempt_count
        task.result = result
        task.save(update_fields=["status", "last_error", "result", "updated_at"])
        _send_cancel_command(task=task)
        _sync_task_info(task)
        redis_store.push_task_stream(
            task_id=str(task.id),
            message=_terminal_stream_message(task),
        )
    from apps.node.services.internal.task_offline_reconcile import (
        sync_platform_tasks_for_node_task,
    )

    if task.correlation_type == "protection.backup.policy_prepare":
        from apps.protection.services.backup_orchestrator import (
            queue_backup_result_projection,
        )

        queue_backup_result_projection(node_task=task)
    else:
        sync_platform_tasks_for_node_task(node_task=task)
    return True


def reconcile_unaccepted_agent_tasks(*, limit: int = 200) -> dict[str, int | bool]:
    """Retry durable ACK commands with their original NodeTask identity."""
    now = timezone.now()
    ack_wait = timezone.timedelta(
        seconds=max(1, node_conf.TASK_COMMAND_ACK_TIMEOUT_SECONDS)
    )
    max_age = timezone.timedelta(
        seconds=max(1, node_conf.TASK_COMMAND_ACK_MAX_AGE_SECONDS)
    )
    max_attempts = 1 + max(0, node_conf.TASK_COMMAND_ACK_MAX_RETRIES)
    ack_task_filter = Q()
    for correlation_type, kind in _ACK_DURABLE_TASKS:
        ack_task_filter |= Q(correlation_type=correlation_type, kind=kind)
    protocol_lookup = f"result__{_DELIVERY_PROTOCOL_KEY}"
    candidate_query = NodeTask.objects.filter(
        ack_task_filter,
        status=NodeTask.Status.PENDING,
        accepted_at__isnull=True,
    ).filter(
        # Keep only an explicit legacy selection out of later ACK sweeps.
        # Missing or unknown values must be reclassified so malformed or
        # future metadata cannot leave a capable task pending forever.
        ~Q(result__has_key=_DELIVERY_PROTOCOL_KEY)
        | (
            Q(result__has_key=_DELIVERY_PROTOCOL_KEY)
            & ~Q(**{protocol_lookup: _DELIVERY_PROTOCOL_LEGACY})
        )
    )
    recovery_hold = redis_store.ws_recovery_hold_active()
    if recovery_hold:
        # Refresh every already-selected ACK command, not only the first
        # reconciliation page. Markerless, never-dispatched work may belong to
        # a legacy Agent and must retain its original watchdog semantics.
        # This makes a blue/green deployment a bounded pause while the Redis
        # hold TTL prevents an unbounded extension after recovery.
        # Refresh only after half of the bounded window is consumed. A long
        # Redis outage therefore causes at most one bulk write per half-window
        # instead of rewriting every pending row on each 15-second tick.
        candidate_query.filter(
            Q(**{protocol_lookup: _DELIVERY_PROTOCOL_ACK})
            | Q(delivery_attempt_count__gt=0),
            watchdog_deadline_at__lt=now + (max_age / 2),
        ).update(
            watchdog_deadline_at=now + max_age,
            updated_at=now,
        )
        return {
            "candidates": 0,
            "redelivered": 0,
            "timed_out": 0,
            "recovery_hold": True,
        }
    candidates = list(
        candidate_query.select_related("node").order_by("created_at", "id")[
            : max(1, int(limit))
        ]
    )
    redelivered = 0
    timed_out = 0
    considered = 0
    for task in candidates:
        if not _persist_delivery_protocol(task=task):
            continue
        considered += 1
        route_state = _node_route_state(task=task)
        if task.watchdog_deadline_at <= now:
            if route_state == _RouteState.ONLINE:
                error_code = "AGENT_ACK_TIMEOUT"
                message = "Agent did not durably accept task.command"
            elif (
                task.node.availability == Node.Availability.ONLINE
                and _last_seen_within_task_route_grace(task.node)
            ):
                error_code = "AGENT_CONNECTION_UNSTABLE"
                message = "Agent connection remained unstable during delivery"
            else:
                error_code = "AGENT_UNAVAILABLE"
                message = "Agent remained unavailable during delivery"
            if _timeout_unaccepted_task(
                task_id=task.id,
                error_code=error_code,
                message=message,
            ):
                timed_out += 1
            continue
        if route_state != _RouteState.ONLINE:
            continue
        since_delivery = now - (task.last_delivery_at or task.created_at)
        if since_delivery < ack_wait:
            continue
        if task.delivery_attempt_count >= max_attempts and since_delivery >= ack_wait:
            if _timeout_unaccepted_task(task_id=task.id):
                timed_out += 1
            continue
        previous_attempts = task.delivery_attempt_count
        delivered = deliver_agent_task(task=task, allow_ack_redelivery=True)
        if delivered.delivery_attempt_count > previous_attempts:
            redelivered += 1
    return {
        "candidates": considered,
        "redelivered": redelivered,
        "timed_out": timed_out,
        "recovery_hold": False,
    }


@transaction.atomic
def dispatch_task(
    *,
    org: Organization,
    node: Node,
    kind: str,
    payload: dict | None = None,
    correlation_type: str = "",
    correlation_id: str = "",
    requesting_organization_id: int | None = None,
    parent_task: Task | None = None,
) -> NodeTask:
    task = create_agent_task(
        org=org,
        node=node,
        kind=kind,
        payload=payload,
        correlation_type=correlation_type,
        correlation_id=correlation_id,
        requesting_organization_id=requesting_organization_id,
        parent_task=parent_task,
    )
    transaction.on_commit(
        lambda bound_task=task: deliver_agent_task(task=bound_task),
    )
    return task


@transaction.atomic
def record_task_progress(
    *,
    task_id: uuid.UUID | str,
    node_id: int,
    progress: dict[str, Any] | None = None,
    alive: bool = False,
) -> NodeTask:
    task = (
        NodeTask.objects.select_for_update().filter(pk=task_id, node_id=node_id).first()
    )
    if task is None:
        raise LookupError("task not found")

    if task.status not in _ACTIVE_STATUSES:
        return task

    now = timezone.now()
    source_nas_probe_started_at = None
    if _is_source_nas_probe(
        correlation_type=task.correlation_type,
        kind=task.kind,
    ):
        source_nas_probe_started_at = _source_nas_probe_started_at(
            task=task,
            fallback=now,
        )
    repository_initialize_started_at = None
    if _is_repository_initialize_task(
        correlation_type=task.correlation_type,
        kind=task.kind,
    ):
        repository_initialize_started_at = (
            task.accepted_at
            or task.dispatched_at
            or task.last_progress_at
            or task.created_at
            or now
        )
    task.status = NodeTask.Status.RUNNING
    task.accepted_at = task.accepted_at or now
    task.last_progress_at = now
    task.result = _without_delivery_runtime_state(task.result)
    if _is_protection_backup_task(task):
        # task.alive and generic task.progress frames prove that the Agent's
        # execution goroutine is still running. Renew the activity lease even
        # when Kopia's byte counters or percentage have not changed.
        task.watchdog_deadline_at = _protection_backup_watchdog_deadline(from_time=now)
        update_fields = [
            "status",
            "accepted_at",
            "last_progress_at",
            "watchdog_deadline_at",
            "result",
            "updated_at",
        ]
    elif _is_source_nas_probe(
        correlation_type=task.correlation_type,
        kind=task.kind,
    ):
        task.watchdog_deadline_at = _source_nas_probe_deadline(
            accepted_at=source_nas_probe_started_at or now
        )
        update_fields = [
            "status",
            "accepted_at",
            "last_progress_at",
            "watchdog_deadline_at",
            "result",
            "updated_at",
        ]
    elif _is_repository_initialize_task(
        correlation_type=task.correlation_type,
        kind=task.kind,
    ):
        # Agent task.alive is emitted by the task wrapper even when a mount or
        # Kopia child is stuck. Repository initialization therefore uses an
        # absolute execution deadline from durable acceptance rather than an
        # activity lease renewed by generic liveness frames.
        task.watchdog_deadline_at = _initial_watchdog_deadline(
            correlation_type=task.correlation_type,
            from_time=repository_initialize_started_at or now,
            kind=task.kind,
        )
        update_fields = [
            "status",
            "accepted_at",
            "last_progress_at",
            "watchdog_deadline_at",
            "result",
            "updated_at",
        ]
    else:
        task.watchdog_deadline_at = _initial_watchdog_deadline(
            correlation_type=task.correlation_type,
            from_time=now,
            kind=task.kind,
        )
        update_fields = [
            "status",
            "accepted_at",
            "last_progress_at",
            "watchdog_deadline_at",
            "result",
            "updated_at",
        ]
    if progress:
        merged = dict(task.result or {})
        _merge_progress_into_result(
            task=task, progress=progress, merged=merged, now=now
        )
        task.result = merged
        if _task_has_detached_marker(task):
            update_fields = list(
                dict.fromkeys(
                    update_fields
                    + _apply_detached_running_state(task=task, merged=merged, now=now)
                )
            )
    task.save(update_fields=update_fields)
    _sync_task_info(task)

    stream_msg = {
        "task_id": str(task.id),
        "status": task.status,
        "alive": alive,
        "progress": progress or {},
    }
    redis_store.push_task_stream(task_id=str(task.id), message=stream_msg)
    return task


@transaction.atomic
def complete_task(
    *,
    task_id: uuid.UUID | str,
    node_id: int,
    status: str,
    result: dict[str, Any] | None = None,
    error: str = "",
    replace_result: bool = False,
) -> NodeTask:
    task = (
        NodeTask.objects.select_for_update().filter(pk=task_id, node_id=node_id).first()
    )
    if task is None:
        raise LookupError("task not found")

    incoming = status.lower()
    if (
        task.status in {NodeTask.Status.FAILED, NodeTask.Status.TIMEOUT}
        and incoming in {"success", "succeeded", "ok"}
        and _is_protection_backup_task(task)
        and not str(
            (result or {}).get("kopia_snapshot_id")
            or (result or {}).get("snapshot_id")
            or ""
        ).strip()
    ):
        logger.warning(
            "late backup success ignored without snapshot identity %s",
            task_log_context(node_id=node_id, task_id=str(task_id), kind=task.kind),
        )
        return task

    incoming_result = dict(result or {})
    incoming_terminal_status = _incoming_terminal_status(incoming)
    if (
        incoming_terminal_status is not None
        and task.status == incoming_terminal_status
        and dict(task.result or {}) == incoming_result
        and _terminal_error_matches(
            task=task,
            incoming=incoming,
            error=error,
        )
    ):
        # This is a byte-semantically identical replay of a result already
        # committed. Keep a transient marker for the WebSocket consumer so it
        # can ACK durable receipt without repeating streams or domain follow-up.
        task._result_retransmission_unchanged = True
        return task

    if task.status in _TERMINAL_STATUSES:
        if isinstance(task.result, dict) and task.result.get("delivery_timeout_sealed"):
            return task
        if task.status == NodeTask.Status.SUCCESS and incoming not in (
            "success",
            "succeeded",
            "ok",
        ):
            return task
        if task.status == NodeTask.Status.CANCELED and incoming not in (
            "canceled",
            "cancelled",
        ):
            return task

    ctx = task_log_context(
        node_id=node_id,
        task_id=str(task_id),
        kind=task.kind,
        correlation_type=task.correlation_type,
        correlation_id=task.correlation_id,
    )

    terminal = status.lower()
    if terminal == "running":
        now = timezone.now()
        task.status = NodeTask.Status.RUNNING
        task.accepted_at = task.accepted_at or now
        if replace_result:
            merged = dict(result or {})
        else:
            merged = _without_delivery_runtime_state(task.result)
            if result:
                merged.update(result)
        task.result = merged
        if error:
            task.last_error = error[:2000]
        update_fields = ["status", "accepted_at", "result", "last_error", "updated_at"]
        if _task_has_detached_marker(task):
            update_fields = list(
                dict.fromkeys(
                    update_fields
                    + _apply_detached_running_state(task=task, merged=merged, now=now)
                )
            )
        task.save(update_fields=update_fields)
        _sync_task_info(task)
        redis_store.push_task_stream(
            task_id=str(task.id),
            message={
                "task_id": str(task.id),
                "status": task.status,
                "result": task.result,
                "error": task.last_error,
                "alive": True,
            },
        )
        return task
    task.accepted_at = task.accepted_at or timezone.now()
    if terminal in ("success", "succeeded", "ok"):
        task.status = NodeTask.Status.SUCCESS
        task.last_error = ""
    elif terminal in ("canceled", "cancelled"):
        task.status = NodeTask.Status.CANCELED
    else:
        task.status = NodeTask.Status.FAILED
        task.last_error = (error or terminal)[:2000]

    if result:
        task.result = result
    else:
        task.result = _without_delivery_runtime_state(task.result)
    task.save(
        update_fields=["status", "accepted_at", "result", "last_error", "updated_at"]
    )
    _sync_task_info(task)
    redis_store.push_task_stream(
        task_id=str(task.id),
        message=_terminal_stream_message(task),
    )
    if task.status == NodeTask.Status.SUCCESS:
        logger.info("agent task completed %s status=%s", ctx, task.status)
    elif task.status == NodeTask.Status.CANCELED:
        logger.info(
            "agent task canceled %s status=%s error=%s",
            ctx,
            task.status,
            task.last_error[:200],
        )
    else:
        logger.warning(
            "agent task failed %s status=%s error=%s",
            ctx,
            task.status,
            (task.last_error or error or terminal)[:500],
        )
    return task


def _incoming_terminal_status(incoming: str) -> str | None:
    if incoming == "running":
        return None
    if incoming in {"success", "succeeded", "ok"}:
        return NodeTask.Status.SUCCESS
    if incoming in {"canceled", "cancelled"}:
        return NodeTask.Status.CANCELED
    return NodeTask.Status.FAILED


def _terminal_error_matches(*, task: NodeTask, incoming: str, error: str) -> bool:
    existing = str(task.last_error or "")
    if task.status == NodeTask.Status.SUCCESS:
        return existing == "" and str(error or "") == ""
    if task.status == NodeTask.Status.CANCELED:
        candidate = str(error or "").strip().lower()
        return (
            candidate in {"", "canceled", "cancelled"}
            or existing == str(error or "")[:2000]
        )
    return existing == str(error or incoming)[:2000]


@transaction.atomic
def cancel_task(
    *,
    task_id: uuid.UUID | str,
    reason: str = "canceled",
) -> NodeTask | None:
    task = NodeTask.objects.select_for_update().filter(pk=task_id).first()
    if task is None:
        return None
    if task.status not in _ACTIVE_STATUSES:
        return task

    task.last_error = reason[:2000]
    if task.status == NodeTask.Status.PENDING:
        task.status = NodeTask.Status.CANCELED
        task.save(update_fields=["status", "last_error", "updated_at"])
        _send_cancel_command(task=task)
        _sync_task_info(task)
        redis_store.push_task_stream(
            task_id=str(task.id),
            message=_terminal_stream_message(task),
        )
        return task

    now = timezone.now()
    merged = dict(task.result or {})
    merged["cancel_requested"] = True
    if task.cancel_requested_at is None:
        task.cancel_requested_at = now
    merged["cancel_requested_at"] = task.cancel_requested_at.isoformat()
    task.result = merged
    task.save(
        update_fields=["result", "last_error", "cancel_requested_at", "updated_at"]
    )
    _send_cancel_command(task=task)
    _sync_task_info(task)
    redis_store.push_task_stream(
        task_id=str(task.id),
        message={
            "task_id": str(task.id),
            "status": task.status,
            "result": task.result,
            "error": task.last_error,
            "cancel_requested": True,
            "alive": True,
        },
    )
    return task


def _project_terminal_node_task(*, task: NodeTask) -> None:
    if task.correlation_type == "protection.backup.policy_prepare":
        from apps.protection.services.backup_orchestrator import (
            queue_backup_result_projection,
        )

        queue_backup_result_projection(node_task=task)
        return

    from apps.node.services.internal.task_offline_reconcile import (
        sync_platform_tasks_for_node_task,
    )

    sync_platform_tasks_for_node_task(node_task=task)


def sweep_cancel_grace_expired(*, limit: int = 500) -> int:
    """Seal cancel requests that an Agent did not acknowledge within the grace window."""
    now = timezone.now()
    cutoff = now - timezone.timedelta(
        seconds=max(1, int(node_conf.TASK_CANCEL_GRACE_SECONDS))
    )
    ids = list(
        NodeTask.objects.filter(
            status__in=_ACTIVE_STATUSES,
            cancel_requested_at__isnull=False,
            cancel_requested_at__lte=cutoff,
        )
        .order_by("cancel_requested_at", "pk")
        .values_list("pk", flat=True)[: max(1, int(limit))]
    )

    marked = 0
    for pk in ids:
        with transaction.atomic():
            task = (
                NodeTask.objects.select_for_update()
                .filter(
                    pk=pk,
                    status__in=_ACTIVE_STATUSES,
                    cancel_requested_at__isnull=False,
                    cancel_requested_at__lte=cutoff,
                )
                .first()
            )
            if task is None:
                continue
            finalized_at = timezone.now()
            merged = dict(task.result or {})
            merged["cancel_requested"] = True
            merged["cancel_requested_at"] = task.cancel_requested_at.isoformat()
            merged["cancel_finalized_by"] = "grace_timeout"
            merged["cancel_finalized_at"] = finalized_at.isoformat()
            task.status = NodeTask.Status.CANCELED
            task.result = merged
            task.save(update_fields=["status", "result", "updated_at"])
            logger.warning(
                "agent task cancellation grace expired %s grace_seconds=%s",
                task_log_context(
                    node_id=task.node_id,
                    task_id=str(task.id),
                    kind=task.kind,
                    correlation_type=task.correlation_type,
                    correlation_id=task.correlation_id,
                ),
                node_conf.TASK_CANCEL_GRACE_SECONDS,
            )
            _sync_task_info(task)
            redis_store.push_task_stream(
                task_id=str(task.id),
                message=_terminal_stream_message(task),
            )
            _project_terminal_node_task(task=task)
            marked += 1
    return marked


def _send_cancel_command(*, task: NodeTask) -> None:
    from apps.node.ws.downlink import send_task_cancel

    try:
        send_task_cancel(task=task)
    except _DOWNLINK_ERRORS as exc:
        logger.warning("cancel send failed task=%s: %s", task.id, exc)


@transaction.atomic
def fail_active_tasks_for_node(*, node_id: int, reason: str) -> int:
    qs = NodeTask.objects.select_for_update().filter(
        node_id=node_id,
        status__in=_ACTIVE_STATUSES,
    )
    count = 0
    for task in qs:
        if _task_has_detached_marker(task):
            continue
        task.status = NodeTask.Status.FAILED
        task.last_error = reason[:2000]
        task.save(update_fields=["status", "last_error", "updated_at"])
        _sync_task_info(task)
        logger.warning(
            "agent task failed (node offline) %s error=%s",
            task_log_context(node_id=node_id, task_id=str(task.id), kind=task.kind),
            reason[:200],
        )
        redis_store.push_task_stream(
            task_id=str(task.id),
            message=_terminal_stream_message(task),
        )
        from apps.node.services.internal.task_offline_reconcile import (
            sync_platform_tasks_for_node_task,
        )

        sync_platform_tasks_for_node_task(node_task=task)
        count += 1
    return count


def sweep_watchdog_timeouts(
    *, queryset: QuerySet[NodeTask] | None = None, limit: int = 500
) -> int:
    now = timezone.now()
    qs = queryset
    if qs is None:
        qs = NodeTask.objects.filter(
            status__in=_ACTIVE_STATUSES,
            watchdog_deadline_at__lt=now,
        )
    ids = list(
        qs.order_by("watchdog_deadline_at", "pk").values_list("pk", flat=True)[
            : int(limit)
        ]
    )
    uplink_activities = redis_store.get_task_uplink_activities(
        task_ids=[str(task_id) for task_id in ids]
    )

    marked = 0
    for pk in ids:
        with transaction.atomic():
            task = (
                NodeTask.objects.select_for_update()
                .filter(
                    pk=pk,
                    status__in=_ACTIVE_STATUSES,
                    watchdog_deadline_at__lt=timezone.now(),
                )
                .first()
            )
            if task is None:
                continue
            # ACK-capable commands have a separate delivery/acceptance
            # watchdog. The reconciliation sweep owns its bounded deadline
            # and structured terminal reason; the execution watchdog must not
            # turn queue wait into a false WATCHDOG_STALL.
            if (
                task.status == NodeTask.Status.PENDING
                and task.accepted_at is None
                and (task.delivery_attempt_count > 0 or task_uses_command_ack(task))
            ):
                continue
            uplink_activity = uplink_activities.get(str(task.id))
            try:
                received_at = float((uplink_activity or {}).get("received_at", 0))
            except (TypeError, ValueError):
                received_at = 0
            activity_age = max(0.0, timezone.now().timestamp() - received_at)
            message_type = str((uplink_activity or {}).get("message_type", ""))
            projection_grace = (
                node_conf.TASK_RESULT_UPLINK_PROJECTION_GRACE_SECONDS
                if "result" in message_type.lower()
                else node_conf.TASK_UPLINK_PROJECTION_GRACE_SECONDS
            )
            bounded_remote_execution = (
                _is_source_nas_probe(
                    correlation_type=task.correlation_type,
                    kind=task.kind,
                )
                or _is_repository_initialize_task(
                    correlation_type=task.correlation_type,
                    kind=task.kind,
                )
            )
            result_projection_pending = "result" in message_type.lower()
            if (
                received_at > 0
                and activity_age < projection_grace
                and (not bounded_remote_execution or result_projection_pending)
            ):
                remaining_grace = max(
                    1,
                    int(projection_grace - activity_age),
                )
                task.watchdog_deadline_at = timezone.now() + timezone.timedelta(
                    seconds=remaining_grace
                )
                task.save(update_fields=["watchdog_deadline_at", "updated_at"])
                logger.info(
                    "agent task watchdog deferred for queued uplink %s "
                    "message_type=%s remaining_grace_seconds=%s",
                    task_log_context(
                        node_id=task.node_id,
                        task_id=str(task.id),
                        kind=task.kind,
                    ),
                    message_type or "unknown",
                    remaining_grace,
                )
                continue
            task.status = NodeTask.Status.TIMEOUT
            if "result" in message_type.lower():
                merged = dict(task.result or {})
                merged["diagnostic_error_code"] = "RESULT_ACK_TIMEOUT"
                task.result = merged
                task.last_error = "result acknowledgement timeout"
                update_fields = ["status", "result", "last_error", "updated_at"]
            else:
                task.last_error = "watchdog timeout (no progress)"
                update_fields = ["status", "last_error", "updated_at"]
            task.save(update_fields=update_fields)
            logger.warning(
                "agent task watchdog timeout %s",
                task_log_context(
                    node_id=task.node_id,
                    task_id=str(task.id),
                    kind=task.kind,
                ),
            )
            _send_cancel_command(task=task)
            _sync_task_info(task)
            redis_store.push_task_stream(
                task_id=str(task.id),
                message=_terminal_stream_message(task),
            )
            from apps.node.services.internal.task_offline_reconcile import (
                sync_platform_tasks_for_node_task,
            )

            sync_platform_tasks_for_node_task(node_task=task)
            marked += 1
    return marked
