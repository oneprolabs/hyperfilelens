"""Domain constants for the node app."""

from apps.node.models import Node, NodeTask

NodeStatus = Node.Status
NodeRole = Node.Role
NodeTaskStatus = NodeTask.Status

# Celery task paths (see ``apps.node.tasks``).
TASK_SWEEP_NODE_TASK_WATCHDOG = (
    "apps.node.tasks.node_task.sweep_node_task_watchdog"
)
TASK_REDELIVER_AGENT_TASK = (
    "apps.node.tasks.node_task.redeliver_agent_task"
)
TASK_RECONCILE_UNACCEPTED_AGENT_TASKS = (
    "apps.node.tasks.node_task.reconcile_unaccepted_agent_tasks"
)
TASK_RECONCILE_STALE_ONLINE_NODES = (
    "apps.node.tasks.node.reconcile_stale_online_nodes"
)
TASK_RECONCILE_NODE_AVAILABILITY = (
    "apps.node.tasks.node.reconcile_node_availability"
)
TASK_ADVANCE_NODE_LIFECYCLE = (
    "apps.node.tasks.lifecycle.advance_node_lifecycle_for_node"
)
TASK_ADVANCE_ACTIVE_LIFECYCLE_NODES = (
    "apps.node.tasks.lifecycle.advance_active_lifecycle_nodes"
)
TASK_INGEST_NODE_UPLINK_STREAMS = (
    "apps.node.tasks.uplink_ingest.ingest_node_uplink_streams"
)
TASK_RECONCILE_OFFLINE_STALE_NODE_TASKS = (
    "apps.node.tasks.offline_reconcile.reconcile_offline_stale_node_tasks_task"
)
TASK_RECONCILE_EXECUTION_STATE = (
    "apps.node.tasks.offline_reconcile.reconcile_execution_state_task"
)
TASK_NOTIFY_AGENT_UPGRADES_AVAILABLE = (
    "apps.node.tasks.upgrade_notification.notify_agent_upgrades_available"
)

__all__ = [
    "NodeRole",
    "NodeStatus",
    "NodeTaskStatus",
    "TASK_ADVANCE_ACTIVE_LIFECYCLE_NODES",
    "TASK_ADVANCE_NODE_LIFECYCLE",
    "TASK_INGEST_NODE_UPLINK_STREAMS",
    "TASK_NOTIFY_AGENT_UPGRADES_AVAILABLE",
    "TASK_RECONCILE_EXECUTION_STATE",
    "TASK_RECONCILE_OFFLINE_STALE_NODE_TASKS",
    "TASK_RECONCILE_NODE_AVAILABILITY",
    "TASK_RECONCILE_STALE_ONLINE_NODES",
    "TASK_REDELIVER_AGENT_TASK",
    "TASK_RECONCILE_UNACCEPTED_AGENT_TASKS",
    "TASK_SWEEP_NODE_TASK_WATCHDOG",
]
