"""
Node registry reconciliation for stale heartbeats and WebSocket instance loss.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node import conf as node_conf
from apps.node.services.internal import redis_store
from apps.node.services.internal.task import fail_active_tasks_for_node

logger = logging.getLogger(__name__)

CONNECTION_ONLINE = Node.Availability.ONLINE
CONNECTION_RECONNECTING = "reconnecting"
CONNECTION_OFFLINE = Node.Availability.OFFLINE
NODE_OPERATION_BLOCKING_STATUSES = frozenset({
    Node.Status.UPGRADING,
    Node.Status.RESTARTING,
    Node.Status.VERIFYING,
    Node.Status.VERIFICATION_PENDING,
    Node.Status.REMOVING,
    Node.Status.CLEANING_UP,
})


def node_is_available_for_work(node: Node) -> bool:
    """A reachable node can execute work unless a lifecycle operation owns it."""
    return (
        node.availability == Node.Availability.ONLINE
        and node.status not in NODE_OPERATION_BLOCKING_STATUSES
    )


def _agent_lease_grace_seconds() -> int:
    return node_conf.AGENT_LOC_TTL_SECONDS


def _last_seen_within_grace(node: Node) -> bool:
    if not node.last_seen_at:
        return False
    grace = timezone.timedelta(seconds=_agent_lease_grace_seconds())
    return timezone.now() - node.last_seen_at < grace


def effective_agent_node_status(node: Node) -> str:
    """Return the effective availability of a node, including WSS lease health."""
    if node.role not in (NodeRole.AGENT, NodeRole.PROXY):
        return node.availability
    if node.availability != Node.Availability.ONLINE:
        return Node.Availability.OFFLINE
    if _agent_routable(agent_id=node.id):
        return Node.Availability.ONLINE
    if _last_seen_within_grace(node):
        return Node.Availability.ONLINE
    return Node.Availability.OFFLINE


def agent_connection_status(node: Node) -> str:
    """Internal task-reconciliation connection state; never expose it via APIs."""
    if node.role not in (NodeRole.AGENT, NodeRole.PROXY):
        return str(node.availability or CONNECTION_OFFLINE)
    if node.availability != Node.Availability.ONLINE:
        return CONNECTION_OFFLINE
    if agent_ws_routable(agent_id=node.id) or _agent_loc_key_exists(agent_id=node.id):
        return CONNECTION_ONLINE
    return CONNECTION_RECONNECTING if _last_seen_within_grace(node) else CONNECTION_OFFLINE


def _within_reconnect_grace(node: Node) -> bool:
    return _last_seen_within_grace(node)


def _agent_loc_key_exists(*, agent_id: int) -> bool:
    """True when Redis still holds an ``agent_loc`` lease for this agent."""
    r = redis_store.get_redis()
    if r is None:
        return True
    return bool(r.exists(redis_store.agent_loc_key(agent_id)))


def agent_session_registered(*, agent_id: int) -> bool:
    """True when the agent has an active WSS session lease (``agent_loc`` key present)."""
    return _agent_loc_key_exists(agent_id=agent_id)


def agent_ws_routable(*, agent_id: int) -> bool:
    """True when the agent has a live WebSocket session in Redis (``agent_loc`` + ws alive)."""
    return _agent_routable(agent_id=agent_id)


def _agent_routable(*, agent_id: int) -> bool:
    ws_instance = redis_store.get_agent_location(agent_id=agent_id)
    if not ws_instance:
        return False
    client = redis_store.get_redis()
    if client is None:
        return True
    return bool(client.exists(redis_store.ws_alive_key(ws_instance)))


def record_node_availability(
    *,
    node_id: int,
    availability: str,
    observed_at=None,
    expected_updated_at=None,
    expected_last_seen_at=None,
) -> bool:
    """Persist one confirmed Node availability observation."""
    if availability not in {
        Node.Availability.ONLINE,
        Node.Availability.OFFLINE,
    }:
        raise ValueError("invalid node availability")
    with transaction.atomic():
        node = Node.objects.select_for_update().filter(pk=node_id).first()
        if node is None:
            return False
        if (
            expected_updated_at is not None
            and (
                node.availability_updated_at != expected_updated_at
                or node.last_seen_at != expected_last_seen_at
            )
        ):
            return False
        observation_time = observed_at or timezone.now()
        if node.availability_updated_at > observation_time:
            return False
        transitioned = node.availability != availability
        node.availability = availability
        node.availability_updated_at = observation_time
        node.save(
            update_fields=[
                "availability",
                "availability_updated_at",
                "updated_at",
            ]
        )

        def _project() -> None:
            try:
                from apps.source.services.internal.availability import (
                    project_node_availability,
                )

                project_node_availability(
                    node_id=node.id,
                    transitioned=transitioned,
                )
            except Exception:
                logger.warning(
                    "node availability projection failed node_id=%s",
                    node.id,
                    exc_info=True,
                )

        transaction.on_commit(_project)
        if transitioned and availability == Node.Availability.ONLINE:

            def _probe_bound_repositories() -> None:
                try:
                    from apps.storage.repositories.models import Repository
                    from apps.storage.tasks import check_storage_repository_health

                    repository_ids = list(
                        Repository.objects.filter(
                            bind_node_id=node.id,
                            health__in=[Repository.Health.OFFLINE, Repository.Health.UNVERIFIED],
                            status=Repository.Status.CREATED,
                        ).values_list("id", flat=True)
                    )
                    for repository_id in repository_ids:
                        check_storage_repository_health.apply_async(
                            kwargs={"repository_id": repository_id}, countdown=2
                        )
                except Exception:
                    logger.warning(
                        "bound repository health recovery dispatch failed node_id=%s",
                        node.id,
                        exc_info=True,
                    )

            transaction.on_commit(_probe_bound_repositories)
        return True


def record_node_available(*, node_id: int, observed_at=None) -> bool:
    return record_node_availability(
        node_id=node_id,
        availability=Node.Availability.ONLINE,
        observed_at=observed_at,
    )


def reconcile_node_availability(*, limit: int = 200) -> dict[str, int | bool | str]:
    """Expire unavailable Agent/Proxy observations only while Redis is healthy."""
    client = redis_store.get_redis()
    if client is None:
        retained = Node.objects.filter(
            role__in=(NodeRole.AGENT, NodeRole.PROXY),
            availability=Node.Availability.ONLINE,
        ).count()
        return {
            "redis_healthy": False,
            "candidates": retained,
            "nodes_marked_offline": 0,
            "retained": retained,
        }

    candidates = list(
        Node.objects.filter(
            role__in=(NodeRole.AGENT, NodeRole.PROXY),
            availability=Node.Availability.ONLINE,
        )
        .order_by("last_seen_at", "id")[: max(1, int(limit))]
    )
    stale: list[tuple[int, object, object]] = []
    try:
        client.ping()
        for node in candidates:
            raw_location = client.get(redis_store.agent_loc_key(node.id))
            routable = False
            if raw_location:
                ws_instance, _session = redis_store._decode_agent_loc(
                    str(raw_location)
                )
                routable = bool(
                    ws_instance
                    and client.exists(redis_store.ws_alive_key(ws_instance))
                )
            if routable or _last_seen_within_grace(node):
                continue
            stale.append(
                (
                    node.id,
                    node.availability_updated_at,
                    node.last_seen_at,
                )
            )
    except Exception as exc:
        logger.warning("node availability reconciliation retained during Redis failure: %s", exc)
        return {
            "redis_healthy": False,
            "candidates": len(candidates),
            "nodes_marked_offline": 0,
            "retained": len(candidates),
        }

    marked = 0
    for node_id, expected_updated_at, expected_last_seen_at in stale:
        if record_node_availability(
            node_id=node_id,
            availability=Node.Availability.OFFLINE,
            expected_updated_at=expected_updated_at,
            expected_last_seen_at=expected_last_seen_at,
        ):
            marked += 1
    return {
        "redis_healthy": True,
        "candidates": len(candidates),
        "nodes_marked_offline": marked,
        "retained": len(candidates) - marked,
        "checked_at": timezone.now().isoformat(),
    }


def reconcile_stale_online_nodes(*, limit: int = 200) -> dict[str, int]:
    """
    Mark available nodes without fresh Redis routing as unavailable.

    Fails in-flight ``NodeTask`` rows on affected nodes (ghost-task cleanup).
    """
    nodes_marked_offline = 0
    tasks_failed = 0
    task_failure_held = not redis_store.offline_task_finalization_ready()

    node_ids = list(
        Node.objects.filter(availability=Node.Availability.ONLINE)
        .order_by("last_seen_at", "id")
        .values_list("pk", flat=True)[: max(1, int(limit))]
    )

    for node_id in node_ids:
        with transaction.atomic():
            node = (
                Node.objects.select_for_update()
                .filter(pk=node_id, availability=Node.Availability.ONLINE)
                .first()
            )
            if node is None:
                continue
            if _agent_routable(agent_id=node.id):
                continue
            if _last_seen_within_grace(node):
                continue

            node.availability = Node.Availability.OFFLINE
            node.availability_updated_at = timezone.now()
            node.save(update_fields=["availability", "availability_updated_at", "updated_at"])
            nodes_marked_offline += 1
            try:
                from apps.source.services.internal.agent_host_sync import sync_agent_source_host

                sync_agent_source_host(node=node)
            except Exception:
                logger.debug("agent source-host sync failed node_id=%s", node.id, exc_info=True)

            failed = 0
            if not task_failure_held:
                failed = fail_active_tasks_for_node(
                    node_id=node.id,
                    reason="agent heartbeat expired (registry reconcile)",
                )
            tasks_failed += failed
            logger.info(
                "node %s marked offline (stale agent_loc); failed_tasks=%s",
                node.id,
                failed,
            )

    return {
        "nodes_marked_offline": nodes_marked_offline,
        "tasks_failed": tasks_failed,
        "task_failure_held": task_failure_held,
        "checked_at": timezone.now().isoformat(),
    }
