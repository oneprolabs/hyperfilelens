"""Resolve default display names for enrolled Agent / Proxy nodes."""

from __future__ import annotations

from typing import Any

from apps.node.models import Node

_AUTO_NODE_NAMES = frozenset({"", "new-node", "node", "new node"})
_NODE_NAME_MAX_LENGTH = Node._meta.get_field("name").max_length


def _bounded_node_name(value: str, *, suffix: str = "") -> str:
    value = str(value or "").strip()
    suffix = str(suffix or "")
    available = max(0, _NODE_NAME_MAX_LENGTH - len(suffix))
    return f"{value[:available].rstrip()}{suffix}"


def hostname_from_metadata(metadata: dict[str, Any] | None) -> str:
    if not isinstance(metadata, dict):
        return ""
    inv = metadata.get("inventory")
    sources: list[dict[str, Any]] = []
    if isinstance(inv, dict):
        sources.append(inv)
    sources.append(metadata)
    for source in sources:
        hostname = str(source.get("hostname") or "").strip()
        if hostname:
            return hostname
    return ""


def runtime_principal_name(metadata: dict[str, Any] | None) -> str:
    if not isinstance(metadata, dict):
        return ""
    principal = metadata.get("runtime_principal")
    if not isinstance(principal, dict):
        return ""
    return str(principal.get("name") or "").strip()


def is_auto_assigned_node_name(name: str | None) -> bool:
    return str(name or "").strip().lower() in _AUTO_NODE_NAMES


def is_automatic_user_node_name(
    *,
    name: str | None,
    metadata: dict[str, Any] | None,
    node_id: int | None = None,
) -> bool:
    """Return whether a user Agent still has a generated display name."""
    current = str(name or "").strip()
    if is_auto_assigned_node_name(current):
        return True

    hostname = hostname_from_metadata(metadata)
    if not hostname:
        return False
    candidates = {_bounded_node_name(hostname)}
    principal = runtime_principal_name(metadata)
    if principal:
        generated = _bounded_node_name(f"{hostname} · {principal}")
        candidates.add(generated)
        if node_id is not None:
            candidates.add(_bounded_node_name(generated, suffix=f"-{node_id}"))
    return current in candidates


def resolve_registration_node_name(
    *, payload: dict[str, Any], fallback: str = "new-node"
) -> str:
    """Prefer hostname from registration metadata over placeholder names."""
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    hostname = hostname_from_metadata(meta)
    installation_mode = str(payload.get("installation_mode") or "").strip()
    principal = runtime_principal_name(meta)
    explicit = str(payload.get("name") or "").strip()
    if hostname:
        if (
            installation_mode
            in (
                Node.InstallationMode.USER,
                Node.InstallationMode.USER_CONTINUOUS,
            )
            and principal
        ):
            return _bounded_node_name(f"{hostname} · {principal}")
        return _bounded_node_name(hostname)
    if explicit and not is_auto_assigned_node_name(explicit):
        return _bounded_node_name(explicit)
    return _bounded_node_name(explicit or fallback)


def resolve_inventory_node_name(*, node: Node, inventory: dict[str, Any]) -> str | None:
    """Return hostname when the node still carries an auto-assigned name."""
    if not is_auto_assigned_node_name(node.name):
        return None
    hostname = str(inventory.get("hostname") or "").strip()
    if not hostname:
        return None
    return hostname


def uniquify_node_name(
    *,
    organization_id: int,
    name: str,
    exclude_node_id: int | None = None,
) -> str:
    name = _bounded_node_name(name)
    qs = Node.objects.filter(
        organization_id=organization_id,
        name=name,
        is_deleted=False,
    )
    if exclude_node_id is not None:
        qs = qs.exclude(pk=exclude_node_id)
    if not qs.exists():
        return name
    if exclude_node_id is not None:
        return _bounded_node_name(name, suffix=f"-{exclude_node_id}")
    return name
