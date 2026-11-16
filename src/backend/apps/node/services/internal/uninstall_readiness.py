"""Current-session capability evidence for remote Agent uninstall."""

from redis.exceptions import RedisError

from apps.node.exceptions import NodeLifecycleError
from apps.node.models import Node
from apps.node.services.internal import redis_store

REQUIRED_UNINSTALL_CAPABILITY = "detached_uninstall_v2"
CAPABILITIES_NOT_READY = "agent_capabilities_not_ready"
CAPABILITIES_UNAVAILABLE = "agent_capabilities_unavailable"


def uninstall_readiness_error(node: Node) -> NodeLifecycleError | None:
    """Never interpret missing or unverified evidence as an unsupported Agent."""
    metadata = node.metadata if isinstance(node.metadata, dict) else {}
    inventory = metadata.get("inventory")
    capabilities = inventory.get("capabilities") if isinstance(inventory, dict) else None
    client = redis_store.get_redis()
    try:
        if client is None:
            raise RedisError("Redis unavailable")
        raw = client.get(redis_store.agent_loc_key(node.id))
        if raw:
            instance, session = redis_store._decode_agent_loc(str(raw))
            routable = instance and client.exists(redis_store.ws_alive_key(instance))
        else:
            routable, session = False, None
    except RedisError:
        return NodeLifecycleError(
            "Agent capability information could not be verified. "
            "Remote uninstall has not been started. Retry when connectivity recovers.",
            code=CAPABILITIES_UNAVAILABLE,
        )
    if not routable:
        return NodeLifecycleError(
            "Node is offline. Strict Cleanup requires the Agent to be reachable.",
            code="node_offline",
        )

    if (
        not session
        or metadata.get("inventory_session_id") != session
        or metadata.get("inventory_capabilities_session_id") != session
        or not isinstance(capabilities, list)
        or not all(isinstance(value, str) and value.strip() for value in capabilities)
    ):
        return NodeLifecycleError(
            "Agent is online, but its current connection's capability information "
            "is not ready. Remote uninstall has not been started. Retry shortly.",
            code=CAPABILITIES_NOT_READY,
        )
    if REQUIRED_UNINSTALL_CAPABILITY not in {value.strip() for value in capabilities}:
        return NodeLifecycleError(
            f'Agent does not report required capability "{REQUIRED_UNINSTALL_CAPABILITY}". '
            "Upgrade the Agent before Strict Cleanup.",
            code="agent_upgrade_required",
        )
    return None


def require_uninstall_capability(node: Node) -> None:
    """Request best-effort ingestion on demand, without waiting for a Worker."""
    error = uninstall_readiness_error(node)
    if error is None:
        return
    if error.code == CAPABILITIES_NOT_READY:
        from apps.node.tasks.uplink_ingest import request_uplink_ingest

        request_uplink_ingest()
    raise error
