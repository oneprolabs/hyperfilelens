from celery.schedules import crontab

from common.scheduling.registry import TASK_REGISTRY


def register_periodic_tasks() -> None:
    TASK_REGISTRY.add(
        name="lens_bridge_reconcile_run_submissions",
        task=(
            "apps.lens_bridge.tasks.run_submission_recovery."
            "reconcile_run_submissions_task"
        ),
        schedule=10,
        kwargs={"limit": 100},
        enabled=True,
        expire_seconds=8,
    )
    TASK_REGISTRY.add(
        name="lens_bridge_reconcile_usage_ledgers",
        task=(
            "apps.lens_bridge.tasks.usage_reconciliation.reconcile_usage_ledgers_task"
        ),
        schedule=30,
        kwargs={"limit": 100},
        enabled=True,
        expire_seconds=25,
    )
    TASK_REGISTRY.add(
        name="lens_bridge_reconcile_gateway_lensnode_provisions",
        task=(
            "apps.lens_bridge.tasks.gateway_provisioning."
            "reconcile_gateway_lensnode_provisions_task"
        ),
        schedule=crontab(minute="*/5"),
        kwargs={"limit": 100},
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="lens_bridge_reconcile_chat_provisions",
        task=(
            "apps.lens_bridge.tasks.chat_lifecycle."
            "reconcile_copilot_chat_provisions_task"
        ),
        schedule=crontab(minute="*/5"),
        kwargs={"limit": 100},
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="lens_bridge_reconcile_knowledge_source_syncs",
        task=(
            "apps.lens_bridge.tasks.knowledge_source_sync."
            "reconcile_knowledge_source_syncs_task"
        ),
        schedule=crontab(minute="*"),
        kwargs={"limit": 100},
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="lens_bridge_reconcile_resource_teardowns",
        task=(
            "apps.lens_bridge.tasks.chat_lifecycle."
            "reconcile_lens_resource_teardowns_task"
        ),
        schedule=crontab(minute="*/5"),
        kwargs={"limit": 100},
        enabled=True,
    )
