"""Validation for remote agent.upgrade dispatch."""

from __future__ import annotations

from apps.node.exceptions import AgentUpgradeError
from apps.node.models import Node
from apps.node.services.internal.agent_release import (
    UPGRADEABLE_AGENT_ROLES,
    agent_version_compare,
    is_agent_artifact_id,
    parse_semver,
    resolve_agent_version,
)


def _merged_inventory(node: Node) -> dict:
    meta = node.metadata if isinstance(node.metadata, dict) else {}
    inv = meta.get("inventory")
    if isinstance(inv, dict):
        return {**meta, **inv}
    return meta


def node_platform_arch(node: Node) -> tuple[str, str]:
    inv = _merged_inventory(node)
    raw_os = str(inv.get("os") or inv.get("platform") or node.os_name or "").lower()
    if "darwin" in raw_os or "mac" in raw_os:
        platform = "darwin"
    elif "windows" in raw_os or raw_os in {"win32", "win64"} or raw_os.startswith("win "):
        platform = "windows"
    else:
        platform = "linux"

    raw_arch = str(inv.get("arch") or "").lower()
    if raw_arch in ("x86_64", "amd64", "x64"):
        arch = "amd64"
    elif raw_arch in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        arch = "amd64"
    return platform, arch


def node_os_version(node: Node) -> str:
    inv = _merged_inventory(node)
    return str(inv.get("os_version") or "").strip()


def validate_agent_upgrade(*, node: Node) -> str:
    """
    Validate node can receive agent.upgrade.

    Returns target release version on success.
    """
    if node.role not in UPGRADEABLE_AGENT_ROLES:
        raise AgentUpgradeError(
            f"role {node.role} does not support remote agent upgrade",
            code="role_not_upgradeable",
        )

    if node.installation_mode == Node.InstallationMode.ACCOUNT:
        raise AgentUpgradeError(
            "specified-user continuous protection requires a local administrator command for upgrade",
            code="local_admin_required",
        )

    if node.availability != Node.Availability.ONLINE:
        raise AgentUpgradeError("node is offline", code="node_offline")

    platform, arch = node_platform_arch(node)
    if node.role in {Node.Role.PROXY, Node.Role.GATEWAY} and platform != "linux":
        raise AgentUpgradeError(
            f"role {node.role} requires linux",
            code="unsupported_platform",
        )

    target = resolve_agent_version(platform, arch, node.role, node_os_version(node))
    if not is_agent_artifact_id(target):
        raise AgentUpgradeError(
            "no published agent release available",
            code="release_unavailable",
        )

    current = str(node.version or "").strip()
    if not current:
        inv = _merged_inventory(node)
        current = str(inv.get("agent_version") or "").strip()

    if current and parse_semver(current) and parse_semver(target):
        if agent_version_compare(current, target) > 0:
            raise AgentUpgradeError(
                f"downgrade not supported ({current} > {target})",
                code="downgrade_not_supported",
            )
    return target


def is_agent_upgrade_kind(kind: str) -> bool:
    normalized = str(kind or "").strip().lower()
    return normalized in {"agent.upgrade", "upgrade.agent"}
