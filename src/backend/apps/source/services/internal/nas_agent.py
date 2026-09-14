"""Dispatch NAS mount/test/unmount tasks to bound Proxy agents."""

from __future__ import annotations

import logging
from typing import Any

from apps.node import agent_paths
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_log import task_log_context
from apps.node.services.interface import run_agent_task_async, run_agent_task_sync
from apps.source.constants import MountStatus, ResourceType
from apps.source.models import SourceResource
from apps.source.services.internal.nas_path_normalize import (
    normalize_nfs_export_path,
    normalize_smb_share,
)
from apps.source.services.internal.source_credentials import (
    resolve_source_credentials,
    scrub_source_secrets,
)
from apps.source.services.internal.availability import record_mount_availability

logger = logging.getLogger(__name__)


class NASAgentError(RuntimeError):
    """Proxy agent failed to handle a NAS task."""


def nas_protocol(config: dict[str, Any]) -> str:
    explicit = str(config.get("protocol") or "").strip().lower()
    if explicit in ("smb", "cifs", "nfs"):
        return "smb" if explicit in ("smb", "cifs") else "nfs"
    if str(config.get("share") or "").strip():
        return "smb"
    if config.get("export_path"):
        return "nfs"
    return "nfs"


def build_nas_agent_payload(
    *,
    resource: SourceResource | None = None,
    resource_type: str = "",
    config: dict | None = None,
    credentials: dict | None = None,
    resource_id: int | None = None,
    mount_point: str = "",
) -> dict[str, Any]:
    cfg = dict(config or (resource.config if resource else {}) or {})
    creds = dict(
        credentials
        or (resolve_source_credentials(resource.credentials) if resource else {})
        or {}
    )
    protocol = nas_protocol(cfg)
    resolved_mount_point = _resolve_nas_agent_mount_point(
        resource=resource,
        mount_point=mount_point,
        config=cfg,
    )
    payload: dict[str, Any] = {
        "resource_id": resource_id or (resource.id if resource else 0),
        "protocol": protocol,
        "server": str(cfg.get("server") or "").strip(),
        "mount_point": resolved_mount_point,
        "options": str(cfg.get("options") or "").strip(),
    }
    if protocol == "smb":
        payload["share"] = normalize_smb_share(str(cfg.get("share") or ""))
        payload["username"] = str(creds.get("username") or "").strip()
        payload["password"] = str(creds.get("password") or "")
        domain = str(creds.get("domain") or "").strip()
        if domain:
            payload["domain"] = domain
    else:
        payload["export_path"] = normalize_nfs_export_path(
            str(cfg.get("export_path") or cfg.get("path") or "")
        )
    storage_type = resource_type or (resource.resource_type if resource else ResourceType.NAS)
    payload["storage_type"] = storage_type
    return payload


def _resolve_nas_agent_mount_point(
    *,
    resource: SourceResource | None,
    mount_point: str,
    config: dict[str, Any],
) -> str:
    explicit = str(mount_point or "").strip()
    if explicit:
        return explicit
    if resource is not None:
        return resource.effective_mount_point()
    return str(config.get("path") or "").strip()


def explain_nas_mount_point_error(
    *,
    resource: SourceResource | None,
    agent_message: str,
    payload_mount_point: str = "",
) -> str:
    """Turn agent-side mount_point validation failures into actionable guidance."""
    msg = str(agent_message or "").strip()
    if "invalid mount_point" not in msg.lower():
        return msg

    mounts_root = agent_paths.agent_mounts_dir()
    if resource is not None:
        config_path = str((resource.config or {}).get("path") or "").strip()
        if config_path:
            try:
                agent_paths.require_agent_mount_path(config_path)
            except ValueError:
                return (
                    f'Mount directory "{config_path}" is outside the proxy agent mount root '
                    f"({mounts_root}/). Update it to a path under {mounts_root}/custom/, "
                    "for example "
                    f"{mounts_root}/custom/nfs-host_export."
                )

    sent = str(payload_mount_point or "").strip()
    if sent:
        return (
            f'Proxy agent rejected mount point "{sent}". '
            f"It must be under {mounts_root}/."
        )
    return f"Proxy agent rejected the mount path. Use a directory under {mounts_root}/."


_KNOWN_AGENT_WS_ERRORS: dict[str, str] = {
    "agent websocket is not routable": (
        "Proxy agent is offline or unreachable. "
        "Use Force Cleanup to record retained residue, or wait until the proxy is online."
    ),
    "agent websocket is reconnecting": (
        "Proxy agent is reconnecting. Wait a moment and try again."
    ),
}


def _humanize_agent_ws_error(message: str) -> str:
    return _KNOWN_AGENT_WS_ERRORS.get(message.strip().lower(), message)


def _task_error_message(outcome) -> str:
    error = str(getattr(outcome.task, "last_error", "") or "").strip()
    if error:
        return _humanize_agent_ws_error(error)
    if isinstance(outcome.stream_message, dict):
        for key in ("error", "message", "detail"):
            value = str(outcome.stream_message.get(key) or "").strip()
            if value:
                return value
    status = str(getattr(outcome.task, "status", "") or "unknown")
    return f"Agent task failed (status: {status})."


def task_error_code(outcome) -> str:
    result = getattr(outcome, "result", None)
    if not isinstance(result, dict):
        return ""
    return str(result.get("error_code") or "").strip()


def _validate_proxy_node(node: Node | None, *, resource: SourceResource | None = None) -> str | None:
    if node is None:
        return "No bound node configured. Please bind a proxy node first."
    if node.role != NodeRole.PROXY:
        return "NAS source must be bound to a proxy node."
    if node.availability != Node.Availability.ONLINE:
        return f'Bound node "{node.name}" is not online.'
    if resource is not None and resource.organization_id != node.organization_id:
        return "Bound node does not belong to this organization."
    return None


def dispatch_nas_agent_task(
    *,
    node: Node,
    kind: str,
    payload: dict[str, Any],
    correlation_type: str,
    correlation_id: str,
    wait_timeout_seconds: int = 120,
):
    nas = payload if kind.startswith("nas.") else payload.get("nas") or payload
    logger.info(
        "nas agent task dispatch %s protocol=%s server=%s resource_id=%s wait_seconds=%s",
        task_log_context(
            node_id=node.id,
            kind=kind,
            correlation_type=correlation_type,
            correlation_id=correlation_id,
        ),
        nas.get("protocol") if isinstance(nas, dict) else "-",
        nas.get("server") if isinstance(nas, dict) else "-",
        nas.get("resource_id") if isinstance(nas, dict) else "-",
        wait_timeout_seconds,
    )
    task_payload = {"nas": payload, **payload}
    persisted_nas = _scrub_nas_task_payload(payload)
    outcome = run_agent_task_sync(
        organization_id=node.organization_id,
        node_id=node.id,
        kind=kind,
        payload=task_payload,
        persisted_payload={"nas": persisted_nas, **persisted_nas},
        correlation_type=correlation_type,
        correlation_id=correlation_id,
        wait_timeout_seconds=wait_timeout_seconds,
    )
    ctx = task_log_context(
        node_id=node.id,
        task_id=str(getattr(outcome.task, "id", "")),
        kind=kind,
        correlation_type=correlation_type,
        correlation_id=correlation_id,
    )
    if outcome.timed_out:
        logger.warning("nas agent task timed out %s", ctx)
    elif outcome.ok:
        logger.info("nas agent task ok %s task_status=%s", ctx, outcome.task.status)
    else:
        logger.warning(
            "nas agent task failed %s task_status=%s error=%s",
            ctx,
            outcome.task.status,
            _task_error_message(outcome)[:500],
        )
    return outcome


def dispatch_nas_agent_task_async(
    *,
    node: Node,
    kind: str,
    payload: dict[str, Any],
    correlation_type: str,
    correlation_id: str,
    persisted_metadata: dict[str, Any] | None = None,
):
    """Persist and dispatch a NAS command without waiting for its result.

    Credentials remain in the protected delivery envelope.  Only scrubbed NAS
    data and caller-owned correlation metadata are visible in ``NodeTask``.
    """

    nas = payload if kind.startswith("nas.") else payload.get("nas") or payload
    logger.info(
        "nas agent task async dispatch %s protocol=%s server=%s resource_id=%s",
        task_log_context(
            node_id=node.id,
            kind=kind,
            correlation_type=correlation_type,
            correlation_id=correlation_id,
        ),
        nas.get("protocol") if isinstance(nas, dict) else "-",
        nas.get("server") if isinstance(nas, dict) else "-",
        nas.get("resource_id") if isinstance(nas, dict) else "-",
    )
    task_payload = {"nas": payload, **payload}
    persisted_nas = _scrub_nas_task_payload(payload)
    persisted_payload = {
        "nas": persisted_nas,
        **persisted_nas,
        **dict(persisted_metadata or {}),
    }
    return run_agent_task_async(
        organization_id=node.organization_id,
        node_id=node.id,
        kind=kind,
        payload=task_payload,
        persisted_payload=persisted_payload,
        correlation_type=correlation_type,
        correlation_id=correlation_id,
    )


def _scrub_nas_task_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return scrub_source_secrets(payload)


def apply_mount_success(resource: SourceResource, result: dict[str, Any]) -> None:
    previous_availability = resource.availability
    mount_point = resource.effective_mount_point()
    resource.mount_status = MountStatus.MOUNTED
    resource.mount_point = mount_point
    resource.mount_error = ""
    space = result.get("space_info") if isinstance(result.get("space_info"), dict) else {}
    if space:
        resource.total_size = int(space.get("total_bytes") or 0)
        resource.used_size = int(space.get("used_bytes") or 0)
        resource.free_size = int(space.get("free_bytes") or 0)
    record_mount_availability(resource=resource, availability="online")
    resource.save(
        update_fields=[
            "mount_status",
            "mount_point",
            "mount_error",
            "total_size",
            "used_size",
            "free_size",
            "availability",
            "availability_updated_at",
            "updated_at",
        ]
    )
    _record_source_availability_event(resource, previous_availability)


def apply_mount_failure(
    resource: SourceResource,
    message: str,
    *,
    availability_confirmed: bool,
) -> None:
    previous_availability = resource.availability
    resource.mount_status = MountStatus.ERROR
    resource.mount_error = message[:2000]
    update_fields = ["mount_status", "mount_error", "updated_at"]
    if availability_confirmed:
        record_mount_availability(resource=resource, availability="offline")
        update_fields.extend(["availability", "availability_updated_at"])
    resource.save(update_fields=update_fields)
    _record_source_availability_event(resource, previous_availability)


def _record_source_availability_event(
    resource: SourceResource,
    previous_availability: str,
) -> None:
    """Record a confirmed source transition after its transaction commits."""
    if resource.availability == previous_availability:
        return
    from apps.monitor.services.events import schedule_availability_event

    schedule_availability_event(
        organization_id=resource.organization_id,
        source="source",
        availability=resource.availability,
        occurred_at=resource.availability_updated_at,
        resource_type="source",
        resource_id=str(resource.id),
        resource_name=resource.name,
        target_path="/protection/backup-sources?tab=host",
        details=resource.mount_error or "",
        metadata={"source_type": resource.resource_type},
    )


def apply_unmount_success(resource: SourceResource) -> None:
    resource.mount_status = MountStatus.UNMOUNTED
    resource.mount_error = ""
    resource.save(update_fields=["mount_status", "mount_error", "updated_at"])


def nas_payload_for_resource(resource: SourceResource) -> dict[str, Any]:
    return build_nas_agent_payload(resource=resource)
