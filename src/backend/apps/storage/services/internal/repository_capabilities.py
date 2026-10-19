"""Capability readiness for new Proxy repository initialization."""

from apps.node.models import Node
from apps.node.services.capabilities import (
    REPOSITORY_OWNERSHIP_CAPABILITY,
    node_supports_capability,
)
from apps.node.services.internal import redis_store

CAPABILITIES_NOT_READY = "AGENT_CAPABILITIES_NOT_READY"


class RepositoryCapabilitiesError(RuntimeError):
    """An initialization precondition, not evidence of a physical failure."""

    def __init__(self, message: str, *, error_code: str):
        super().__init__(message)
        self.error_code = error_code


def require_repository_ownership_capability(node: Node) -> None:
    """Distinguish unprojected/current-session evidence from explicit absence."""
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory")
    inventory = inventory if isinstance(inventory, dict) else {}
    capabilities = inventory.get("capabilities")
    session = redis_store.get_agent_session(agent_id=node.id)
    if (
        not session
        or metadata.get("inventory_session_id") != session
        or metadata.get("inventory_capabilities_session_id") != session
        or not isinstance(capabilities, list)
        or not all(isinstance(value, str) for value in capabilities)
    ):
        raise RepositoryCapabilitiesError(
            f'Proxy "{node.name}" is online, but its capability information '
            "is not ready. Repository initialization has not been dispatched.",
            error_code=CAPABILITIES_NOT_READY,
        )
    if not node_supports_capability(node, REPOSITORY_OWNERSHIP_CAPABILITY):
        raise RepositoryCapabilitiesError(
            f'Agent "{node.name}" must be upgraded before creating this repository.',
            error_code="AGENT_UPGRADE_REQUIRED",
        )
