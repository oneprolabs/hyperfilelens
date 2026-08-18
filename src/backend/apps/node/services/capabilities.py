"""Public helpers for Agent capability contracts."""

from __future__ import annotations

from collections.abc import Iterable

from apps.node.models import Node


def node_capabilities(node: Node) -> frozenset[str]:
    """Return normalized capabilities from the latest Agent inventory."""

    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    values = inventory.get("capabilities", metadata.get("capabilities", []))
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        value
        for raw in values
        if (value := str(raw or "").strip())
    )


def missing_node_capabilities(
    node: Node,
    required: Iterable[str],
) -> frozenset[str]:
    """Return capabilities required by a workflow but absent on the Agent."""

    expected = frozenset(
        value
        for raw in required
        if (value := str(raw or "").strip())
    )
    return expected - node_capabilities(node)


__all__ = ["missing_node_capabilities", "node_capabilities"]
