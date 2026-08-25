"""Register restore projection recovery after worker restarts or lost callbacks."""

from common.scheduling.registry import TASK_REGISTRY


def register_periodic_tasks() -> None:
    TASK_REGISTRY.add(
        name="restore_reconcile_node_task_projections",
        task="apps.restore.tasks.reconcile_restore_node_task_projections",
        schedule=60,
        args=(),
        kwargs={"limit": 200},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="restore_reconcile_direct_nas_mounts",
        task="apps.restore.tasks.reconcile_direct_nas_mounts",
        schedule=60,
        args=(),
        kwargs={"limit": 200},
        queue=None,
        enabled=True,
    )
