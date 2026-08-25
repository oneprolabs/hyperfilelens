"""Project SourceLens-admin LensNodes into HFL Data Gateway views."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from apps.lens_bridge.models import LensGatewayLink, LensKnowledgeSource
from apps.lens_bridge.services import gateway_readiness, provisioning, sl_client
from apps.node.api.serializers.node import NodeSerializer
from apps.node.services.internal.agent_upgrade import node_agent_release_status
from apps.node.services.internal.node_lifecycle import enrich_node_row

_SYSTEM_LENSNODE_NAMES = {"local-dev-lensnode"}


def _lensnode_rows() -> list[dict[str, Any]]:
    """Return the full SL-admin LensNode directory, accepting DRF pagination."""
    payload = sl_client.request_json("GET", "/api/lens/admin/lensnodes/")
    if isinstance(payload, dict):
        payload = payload.get("results", payload.get("items", []))
    if not isinstance(payload, Iterable) or isinstance(payload, (str, bytes, dict)):
        return []
    return [dict(item) for item in payload if isinstance(item, dict) and item.get("uuid")]


def _link_index(*, owner_user=None) -> dict[str, LensGatewayLink]:
    links = LensGatewayLink.objects.filter(sl_lensnode_uuid__isnull=False).select_related(
        "gateway", "organization", "owner_user"
    )
    if owner_user is not None:
        links = links.filter(
            owner_user=owner_user,
            scope=LensGatewayLink.GatewayScope.USER,
            origin=LensGatewayLink.Origin.USER,
        )
    return {str(link.sl_lensnode_uuid): link for link in links}


def _sl_status(value: Any) -> str:
    return str(value or "offline").strip().lower() or "offline"


def _origin_for_unlinked(sl_node: dict[str, Any]) -> str:
    if str(sl_node.get("name") or "").strip() in _SYSTEM_LENSNODE_NAMES:
        return LensGatewayLink.Origin.SYSTEM
    return LensGatewayLink.Origin.EXTERNAL


def _serialize_row(
    *,
    position: int,
    sl_node: dict[str, Any],
    link: LensGatewayLink | None,
    user=None,
    release_targets: dict[tuple[str, str, str, str], str] | None = None,
) -> dict[str, Any]:
    sl_uuid = str(sl_node["uuid"])
    sl_status = _sl_status(sl_node.get("status"))
    if link is not None:
        provisioning.apply_gateway_lensnode_snapshot(link, sl_node)
        node = link.gateway
        enrichment = enrich_node_row(org=node.organization, node=node, user=user)
        enrichment["agent_release"] = node_agent_release_status(
            node,
            target_cache=release_targets,
        )
        node_payload = NodeSerializer(
            node,
            context={"enrichments": {node.id: enrichment}},
        ).data
        knowledge_source_count = LensKnowledgeSource.objects.filter(gateway=node).count()
        origin = link.origin
        owner_user = link.owner_user
        workspace_root = str(sl_node.get("workspace_path") or link.resolved_workspace_root())
        sidecar_status = link.sidecar_status
        is_platform_default = bool(link.is_platform_default)
        runtime_state = gateway_readiness.gateway_runtime_state(
            link,
            sl_runtime_status=sl_status,
        )
        gateway_link_id = link.id
    else:
        node_payload = {
            "id": -(position + 1),
            "organization": 0,
            "name": str(sl_node.get("name") or sl_uuid),
            "role": "gateway",
            "status": sl_status,
            "routable": sl_status == "online",
            "version": str(sl_node.get("agent_version") or ""),
            "os_name": "",
            "ip_address": None,
            "metadata": {},
            "created_at": sl_node.get("created_at") or "",
            "updated_at": sl_node.get("updated_at") or "",
            "is_deleted": False,
            "deleted_at": None,
            "lifecycle": None,
            "workload": None,
            "agent_release": None,
        }
        knowledge_source_count = 0
        origin = _origin_for_unlinked(sl_node)
        owner_user = None
        workspace_root = str(sl_node.get("workspace_path") or "")
        sidecar_status = LensGatewayLink.SidecarStatus.NOT_DEPLOYED
        is_platform_default = False
        runtime_state = gateway_readiness.gateway_runtime_state(
            None,
            sl_runtime_status=sl_status,
        )
        gateway_link_id = None

    tasks = sl_node.get("tasks") or []
    return {
        **node_payload,
        "name": str(sl_node.get("name") or node_payload["name"]),
        "status": sl_status,
        "ai_enabled": runtime_state["hfl_usable"],
        "sl_lensnode_uuid": sl_uuid,
        "lensnode_status": sl_status,
        "knowledge_source_count": knowledge_source_count,
        "workspace_root": workspace_root,
        "sidecar_status": sidecar_status,
        "scope": link.scope if link else "",
        "origin": origin,
        "gateway_link_id": gateway_link_id,
        "managed_by_hfl": runtime_state["hfl_managed"],
        **runtime_state,
        "owner_user_id": owner_user.id if owner_user else None,
        "owner_username": getattr(owner_user, "username", "") if owner_user else "",
        "owner_organization_id": link.organization_id if link else None,
        "is_platform_default": is_platform_default,
        "sl_name": str(sl_node.get("name") or ""),
        "sl_status": sl_status,
        "sl_workspace_path": str(sl_node.get("workspace_path") or ""),
        "sl_agent_version": str(sl_node.get("agent_version") or ""),
        "sl_last_heartbeat_at": sl_node.get("last_heartbeat_at"),
        "sl_registered_at": sl_node.get("registered_at"),
        "sl_tasks": tasks if isinstance(tasks, list) else [],
    }


def list_admin_gateway_insight_rows(*, user=None) -> list[dict[str, Any]]:
    """All SL-admin LensNodes with optional HFL ownership metadata."""
    links = _link_index()
    release_targets: dict[tuple[str, str, str, str], str] = {}
    return [
        _serialize_row(
            position=index,
            sl_node=sl_node,
            link=links.get(str(sl_node["uuid"])),
            user=user,
            release_targets=release_targets,
        )
        for index, sl_node in enumerate(_lensnode_rows())
    ]


def list_user_gateway_insight_rows(*, user) -> list[dict[str, Any]]:
    """Only DGs owned by the current HFL user, with live SL status."""
    links = _link_index(owner_user=user)
    if not links:
        return []
    release_targets: dict[tuple[str, str, str, str], str] = {}
    return [
        _serialize_row(
            position=index,
            sl_node=sl_node,
            link=links.get(str(sl_node["uuid"])),
            user=user,
            release_targets=release_targets,
        )
        for index, sl_node in enumerate(_lensnode_rows())
        if str(sl_node["uuid"]) in links
    ]
