"""Periodic task: notify org members when agent upgrades are available."""

from __future__ import annotations

import logging

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_upgrade import validate_agent_upgrade
from apps.notification.services.internal.in_app import publish_to_org_members

logger = logging.getLogger(__name__)

UPGRADEABLE_ROLES = {NodeRole.AGENT, NodeRole.PROXY, NodeRole.GATEWAY}


def _get_nodes_with_available_upgrades() -> dict[int, list[dict]]:
    """Return mapping of org_id -> list of nodes with available upgrades."""
    result: dict[int, list[dict]] = {}

    nodes = Node.objects.filter(
        is_deleted=False,
        role__in=UPGRADEABLE_ROLES,
        availability=Node.Availability.ONLINE,
    ).select_related("organization")

    for node in nodes:
        try:
            target_version = validate_agent_upgrade(node=node)
        except Exception:
            continue

        current = node.version or ""
        if not current:
            continue

        result.setdefault(node.organization_id, []).append(
            {
                "node_id": node.id,
                "node_name": node.name,
                "current_version": current,
                "target_version": target_version,
            }
        )

    return result


def notify_agent_upgrades_available() -> None:
    """Check all orgs for nodes with available upgrades and create notifications."""
    org_nodes = _get_nodes_with_available_upgrades()
    if not org_nodes:
        return

    total_nodes = sum(len(nodes) for nodes in org_nodes.values())
    logger.info(
        "notify_agent_upgrades_available: found %d nodes across %d orgs",
        total_nodes,
        len(org_nodes),
    )

    for org_id, nodes in org_nodes.items():
        for node_info in nodes:
            publish_to_org_members(
                organization_id=org_id,
                event_type="agent.upgrade.available",
                source_type="node",
                source_id=str(node_info["node_id"]),
                title=f"Agent upgrade available for {node_info['node_name']}",
                summary=f"Version {node_info['target_version']} is available (current: {node_info['current_version']})",
                severity="info",
                target_url="/protection/backup-sources?tab=host",
            )
