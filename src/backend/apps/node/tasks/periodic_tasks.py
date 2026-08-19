"""
Register Celery Beat schedules for node communication hygiene:

- Run the periodic ``NodeTask`` watchdog sweep.
- Reconcile ``online`` nodes when Agent or WebSocket heartbeat keys expire.
"""

from apps.node import conf as node_conf
from apps.node.constants import (
    TASK_ADVANCE_ACTIVE_LIFECYCLE_NODES,
    TASK_INGEST_NODE_UPLINK_STREAMS,
    TASK_NOTIFY_AGENT_UPGRADES_AVAILABLE,
    TASK_RECONCILE_EXECUTION_STATE,
    TASK_RECONCILE_NODE_AVAILABILITY,
    TASK_RECONCILE_UNACCEPTED_AGENT_TASKS,
    TASK_RECONCILE_OFFLINE_STALE_NODE_TASKS,
    TASK_RECONCILE_STALE_ONLINE_NODES,
    TASK_SWEEP_NODE_TASK_WATCHDOG,
)
from common.scheduling.registry import TASK_REGISTRY


def register_periodic_tasks() -> None:
    TASK_REGISTRY.add(
        name="node_reconcile_unaccepted_agent_tasks",
        task=TASK_RECONCILE_UNACCEPTED_AGENT_TASKS,
        schedule=node_conf.TASK_COMMAND_ACK_RECONCILE_INTERVAL_SECONDS,
        args=(),
        kwargs={"limit": 200},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="node_sweep_node_task_watchdog",
        task=TASK_SWEEP_NODE_TASK_WATCHDOG,
        schedule=node_conf.WATCHDOG_SWEEP_INTERVAL_SECONDS,
        args=(),
        kwargs={"limit": 500},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="node_reconcile_availability",
        task=TASK_RECONCILE_NODE_AVAILABILITY,
        schedule=node_conf.STALE_NODE_RECONCILE_INTERVAL_SECONDS,
        args=(),
        kwargs={"limit": 200},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="node_reconcile_stale_online_nodes",
        task=TASK_RECONCILE_STALE_ONLINE_NODES,
        schedule=node_conf.STALE_NODE_RECONCILE_INTERVAL_SECONDS,
        args=(),
        kwargs={"limit": 200},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="node_reconcile_offline_stale_node_tasks",
        task=TASK_RECONCILE_OFFLINE_STALE_NODE_TASKS,
        schedule=node_conf.STALE_NODE_RECONCILE_INTERVAL_SECONDS,
        args=(),
        kwargs={"limit": 100},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="node_reconcile_execution_state",
        task=TASK_RECONCILE_EXECUTION_STATE,
        schedule=node_conf.STALE_NODE_RECONCILE_INTERVAL_SECONDS,
        args=(),
        kwargs={"limit": 200},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="node_advance_active_lifecycle_nodes",
        task=TASK_ADVANCE_ACTIVE_LIFECYCLE_NODES,
        schedule=node_conf.LIFECYCLE_ADVANCE_INTERVAL_SECONDS,
        args=(),
        kwargs={},
        queue="node.lifecycle",
        enabled=True,
        expire_seconds=node_conf.LIFECYCLE_ADVANCE_EXPIRE_SECONDS,
    )
    TASK_REGISTRY.add(
        name="node_ingest_uplink_streams",
        task=TASK_INGEST_NODE_UPLINK_STREAMS,
        schedule=node_conf.UPLINK_INGEST_INTERVAL_SECONDS,
        args=(),
        kwargs={},
        queue="node.ingest",
        enabled=True,
        expire_seconds=node_conf.UPLINK_INGEST_EXPIRE_SECONDS,
    )
    TASK_REGISTRY.add(
        name="node_notify_agent_upgrades_available",
        task=TASK_NOTIFY_AGENT_UPGRADES_AVAILABLE,
        schedule=6 * 3600,  # every 6 hours
        args=(),
        kwargs={},
        queue=None,
        enabled=True,
        expire_seconds=600,
    )
