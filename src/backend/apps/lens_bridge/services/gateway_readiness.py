"""HFL Gateway Bundle readiness checks for SourceLens LensNode bindings."""

from __future__ import annotations

from typing import Any

from apps.lens_bridge.models import LensGatewayLink
from apps.node.models.base import NodeRole
from apps.node.services.capabilities import (
    INSIGHT_SAFE_RESTORE_CAPABILITY,
)
from apps.node.services.internal.node_registry import agent_ws_routable
from apps.node.services.internal.redis_store import get_agent_session


READINESS_READY = "ready"
READINESS_NOT_MANAGED = "not_managed"
READINESS_AGENT_OFFLINE = "agent_offline"
READINESS_LENSNODE_OFFLINE = "lensnode_offline"
READINESS_CAPABILITIES_SYNCING = "capabilities_syncing"
READINESS_NOT_COPILOT_ELIGIBLE = "not_copilot_eligible"


def _session_inventory_capabilities_ready(
    *, link: LensGatewayLink, agent_session: str | None
) -> bool:
    """Require capability evidence from the currently routed Agent session."""
    if not agent_session:
        return False
    metadata = link.gateway.metadata if isinstance(link.gateway.metadata, dict) else {}
    if metadata.get("inventory_session_id") != agent_session:
        return False
    if metadata.get("inventory_capabilities_session_id") != agent_session:
        return False
    inventory = metadata.get("inventory")
    if not isinstance(inventory, dict):
        return False
    capabilities = inventory.get("capabilities")
    if not isinstance(capabilities, (list, tuple, set, frozenset)):
        return False
    return INSIGHT_SAFE_RESTORE_CAPABILITY in {
        str(value).strip() for value in capabilities if str(value or "").strip()
    }


def _readiness_reason(
    *,
    link: LensGatewayLink | None,
    hfl_managed: bool,
    hfl_agent_online: bool,
    hfl_sidecar_online: bool,
    copilot_eligible: bool,
    hfl_agent_capabilities_ready: bool,
) -> str:
    if copilot_eligible:
        return READINESS_READY
    if not hfl_managed:
        return READINESS_NOT_MANAGED
    if not hfl_agent_online:
        return READINESS_AGENT_OFFLINE
    if not link or not link.sl_lensnode_uuid or not hfl_sidecar_online:
        return READINESS_LENSNODE_OFFLINE
    if not hfl_agent_capabilities_ready:
        return READINESS_CAPABILITIES_SYNCING
    return READINESS_NOT_COPILOT_ELIGIBLE


def gateway_runtime_state(
    link: LensGatewayLink | None,
    *,
    sl_runtime_status: str = "",
) -> dict[str, Any]:
    """Return the HFL runtime eligibility state for a SourceLens LensNode.

    A SourceLens LensNode becomes HFL-usable only after the complete HFL
    Gateway Bundle is installed: a registered HFL gateway agent, a live HFL
    control channel, and an online HFL-managed LensNode sidecar.
    """
    hfl_managed = bool(
        link
        and link.gateway_id
        and link.gateway.role == NodeRole.GATEWAY
    )
    hfl_agent_online = bool(
        hfl_managed and agent_ws_routable(agent_id=link.gateway_id)
    )
    agent_session = (
        get_agent_session(agent_id=link.gateway_id) if hfl_agent_online else None
    )
    hfl_agent_capabilities_ready = bool(
        hfl_managed
        and _session_inventory_capabilities_ready(
            link=link,
            agent_session=agent_session,
        )
    )
    if (
        hfl_agent_capabilities_ready
        and get_agent_session(agent_id=link.gateway_id) != agent_session
    ):
        # Avoid accepting a capability snapshot if the Agent route changed
        # while this readiness check was reading PostgreSQL.
        hfl_agent_capabilities_ready = False
    hfl_sidecar_online = bool(
        hfl_managed
        and link.sl_lensnode_uuid
        and link.sidecar_status == LensGatewayLink.SidecarStatus.ONLINE
    )
    hfl_usable = bool(
        hfl_managed
        and link
        and link.sl_lensnode_uuid
        and hfl_agent_online
        and hfl_sidecar_online
    )
    copilot_eligible = bool(
        hfl_usable
        and hfl_agent_capabilities_ready
        and link
        and link.origin
        in {LensGatewayLink.Origin.USER, LensGatewayLink.Origin.PLATFORM}
    )
    readiness_reason = _readiness_reason(
        link=link,
        hfl_managed=hfl_managed,
        hfl_agent_online=hfl_agent_online,
        hfl_sidecar_online=hfl_sidecar_online,
        copilot_eligible=copilot_eligible,
        hfl_agent_capabilities_ready=hfl_agent_capabilities_ready,
    )
    return {
        "sl_runtime_status": str(sl_runtime_status or "offline"),
        "hfl_managed": hfl_managed,
        "hfl_agent_online": hfl_agent_online,
        "hfl_agent_capabilities_ready": hfl_agent_capabilities_ready,
        "hfl_sidecar_online": hfl_sidecar_online,
        "hfl_usable": hfl_usable,
        "copilot_eligible": copilot_eligible,
        "readiness_reason": readiness_reason,
    }


def require_hfl_usable_gateway(
    link: LensGatewayLink,
    *,
    field: str = "gateway_link_id",
) -> None:
    """Raise a stable API validation error when a gateway bundle is incomplete."""
    from rest_framework.exceptions import ValidationError

    state = gateway_runtime_state(link)
    if not state["hfl_managed"]:
        raise ValidationError({field: "This SourceLens data gateway is not managed by HFL."})
    if not state["hfl_agent_online"]:
        raise ValidationError({field: "HFL Gateway Agent is offline or not routable."})
    if not state["hfl_sidecar_online"]:
        raise ValidationError({field: "HFL Gateway LensNode sidecar is not online."})


def require_copilot_gateway(
    link: LensGatewayLink,
    *,
    field: str = "gateway_link_id",
) -> None:
    """Raise when a HFL-ready gateway is not eligible for Copilot selection."""
    require_hfl_usable_gateway(link, field=field)
    state = gateway_runtime_state(link)
    if not state["hfl_agent_capabilities_ready"]:
        from rest_framework.exceptions import ValidationError

        raise ValidationError(
            {
                field: (
                    "Data Gateway Agent capabilities are not synchronized yet. "
                    "Wait for the Agent to reconnect, then try again."
                )
            }
        )
    if not state["copilot_eligible"]:
        from rest_framework.exceptions import ValidationError

        raise ValidationError({field: "This data gateway is not eligible for HFL Copilot."})
