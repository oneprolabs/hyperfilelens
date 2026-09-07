"""Register periodic tasks for monitor app."""

from celery.schedules import crontab

from common.scheduling.registry import TASK_REGISTRY


def register_periodic_tasks():
    TASK_REGISTRY.add(
        name="monitor_collect_system_metrics",
        task="apps.monitor.tasks.metrics.collect_system_metrics",
        schedule=crontab(minute="*/5"),
        args=(),
        kwargs={},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="monitor_cleanup_old_system_metrics",
        task="apps.monitor.tasks.metrics.cleanup_old_system_metrics",
        schedule=crontab(hour=3, minute=15),
        args=(),
        kwargs={"days_to_keep": 7},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="monitor_snapshot_repository_metrics",
        task="apps.monitor.tasks.metrics.snapshot_repository_metrics",
        schedule=crontab(minute="*/5"),
        args=(),
        kwargs={},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="monitor_cleanup_old_resource_metrics",
        task="apps.monitor.tasks.metrics.cleanup_old_resource_metrics",
        schedule=crontab(hour=3, minute=45),
        args=(),
        kwargs={"days_to_keep": 14},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="monitor_cleanup_repository_usage_history",
        task="apps.monitor.tasks.metrics.cleanup_repository_usage_history",
        schedule=crontab(hour=4, minute=5),
        args=(),
        kwargs={"days_to_keep": 30, "batch_size": 2000},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="monitor_cleanup_operational_events",
        task="apps.monitor.tasks.metrics.cleanup_operational_events",
        schedule=crontab(hour=4, minute=15),
        args=(),
        kwargs={"days_to_keep": 90, "batch_size": 2000},
        queue=None,
        enabled=True,
    )
