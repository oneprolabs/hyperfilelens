"""Shared health-only payload policy for synchronous and durable observations."""

from apps.node.services.capabilities import (
    REPOSITORY_LIGHTWEIGHT_HEALTH_CAPABILITY,
    node_supports_capability,
)
from apps.storage.conf import (
    repository_health_check_mode,
    repository_health_timeout_seconds,
)
from .repository_errors import RepositoryHealthTransportUnconfirmed


def repository_health_options(node):
    mode = repository_health_check_mode()
    if mode == "lightweight" and not node_supports_capability(
        node,
        REPOSITORY_LIGHTWEIGHT_HEALTH_CAPABILITY,
    ):
        raise RepositoryHealthTransportUnconfirmed(
            "Execution node requires an upgrade for lightweight repository health."
        )
    return {
        "health_check_mode": mode,
        "health_timeout_seconds": repository_health_timeout_seconds(),
    }
