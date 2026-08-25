"""Sync enrolled Agent nodes into local SourceResource rows for the source-host UI."""

from __future__ import annotations

from typing import Any

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_release import latest_published_agent_version
from apps.node.services.internal.node_naming import is_auto_assigned_node_name
from apps.source.constants import ResourceStatus, ResourceType, SelectableSourceKind
from apps.source.models import SourceResource
from apps.source.services.internal.source_pipeline import ensure_pipeline_entry


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _merged_inventory(node: Node) -> dict[str, Any]:
    meta = node.metadata if isinstance(node.metadata, dict) else {}
    inv = meta.get("inventory")
    if isinstance(inv, dict):
        return {**meta, **inv}
    return meta


def _latest_agent_version() -> str:
    return latest_published_agent_version()


def _agent_platform(node: Node, inv: dict[str, Any]) -> str:
    raw = (
        str(inv.get("os") or inv.get("platform") or node.os_name or "").strip().lower()
    )
    if "darwin" in raw or "mac" in raw:
        return "macos"
    if "windows" in raw or raw in {"win32", "win64"} or raw.startswith("win "):
        return "windows"
    return "linux"


def _agent_display_name(node: Node, hostname: str) -> str:
    """Keep per-user Agent instances distinguishable on the source-host UI."""
    if node.installation_mode in (
        Node.InstallationMode.USER,
        Node.InstallationMode.USER_CONTINUOUS,
    ):
        return str(node.name or "").strip() or hostname
    return hostname or str(node.name or "").strip()


def sync_agent_source_host(*, node: Node) -> SourceResource | None:
    """Upsert a local SourceResource bound to an Agent node (idempotent)."""
    if node.role != NodeRole.AGENT:
        return None

    inv = _merged_inventory(node)
    hostname = str(inv.get("hostname") or node.name or "").strip()
    root_path = str(inv.get("root_path") or "/").strip() or "/"
    capabilities = inv.get("capabilities")
    storage_pending = (
        isinstance(capabilities, list)
        and "storage_inventory_v1" in capabilities
        and (
            inv.get("storage_inventory_status") != "ready"
            or not isinstance(inv.get("local_storage_pools"), list)
        )
    )
    total = 0 if storage_pending else _as_int(inv.get("disk_total_bytes"))
    used = 0 if storage_pending else _as_int(inv.get("disk_used_bytes"))
    free = 0 if storage_pending else _as_int(inv.get("disk_free_bytes"))
    if not free and total >= used:
        free = total - used

    config = {
        "root_path": root_path,
        "host_ip": str(node.ip_address or inv.get("host_ip") or ""),
        "agent_version": str(node.version or inv.get("agent_version") or ""),
        "latest_version": _latest_agent_version(),
        "hostname": hostname,
        "platform": _agent_platform(node, inv),
        "arch": str(inv.get("arch") or ""),
        "kopia_version": str(inv.get("kopia_version") or ""),
    }

    # The source lifecycle is independent from the Agent connection state.
    status = ResourceStatus.ACTIVE

    qs = SourceResource.objects.filter(
        organization_id=node.organization_id,
        bound_node=node,
        resource_type=ResourceType.LOCAL,
    )
    resource = qs.first()
    display_name = _agent_display_name(node, hostname)
    if resource is None:
        name = display_name or f"agent-{node.id}"
        if (
            SourceResource.objects.filter(
                organization_id=node.organization_id,
                name=name,
            )
            .exclude(bound_node=node)
            .exists()
        ):
            name = f"{name}-{node.id}"
        resource = SourceResource.objects.create(
            organization_id=node.organization_id,
            name=name,
            resource_type=ResourceType.LOCAL,
            config=config,
            bound_node=node,
            status=status,
            availability=node.availability,
            availability_updated_at=node.availability_updated_at,
            total_size=total,
            used_size=used,
            free_size=free,
        )
        ensure_pipeline_entry(
            organization_id=node.organization_id,
            source_kind=SelectableSourceKind.AGENT,
            ref_id=node.id,
        )
        return resource

    resource.config = config
    resource.status = status
    resource.availability = node.availability
    resource.availability_updated_at = node.availability_updated_at
    resource.total_size = total
    resource.used_size = used
    resource.free_size = free
    update_fields = [
        "config",
        "status",
        "availability",
        "availability_updated_at",
        "total_size",
        "used_size",
        "free_size",
        "updated_at",
    ]
    desired_name = display_name
    if desired_name and (
        is_auto_assigned_node_name(resource.name)
        or resource.name == f"agent-{node.id}"
        or (
            node.installation_mode
            in (
                Node.InstallationMode.USER,
                Node.InstallationMode.USER_CONTINUOUS,
            )
            and resource.name == hostname
        )
    ):
        next_name = desired_name
        if (
            SourceResource.objects.filter(
                organization_id=node.organization_id,
                name=next_name,
            )
            .exclude(pk=resource.pk)
            .exists()
        ):
            next_name = f"{next_name}-{node.id}"
        if next_name != resource.name:
            resource.name = next_name
            update_fields.append("name")
    resource.save(update_fields=update_fields)
    ensure_pipeline_entry(
        organization_id=node.organization_id,
        source_kind=SelectableSourceKind.AGENT,
        ref_id=node.id,
    )
    return resource


def sync_agent_source_hosts_for_org(*, organization_id: int) -> None:
    """Ensure every enrolled Agent node has a local SourceResource row."""
    for node in Node.objects.filter(
        organization_id=organization_id,
        role=NodeRole.AGENT,
        is_deleted=False,
    ):
        try:
            sync_agent_source_host(node=node)
        except Exception:
            continue


def sync_agent_source_host_by_id(*, node_id: int) -> None:
    node = Node.objects.filter(pk=node_id).first()
    if node is None:
        return
    sync_agent_source_host(node=node)
