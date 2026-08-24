"""
Register periodic tasks for storage reconciliation.
"""

from celery.schedules import crontab, schedule

from common.scheduling.registry import TASK_REGISTRY

from apps.storage.conf import repository_health_interval_seconds
from apps.storage.services.internal.repository_operations import maintenance_settings


def register_periodic_tasks():
    settings = maintenance_settings()
    TASK_REGISTRY.add(
        name="storage_cleanup_provider_validations",
        task="apps.storage.tasks.cleanup_expired_storage_provider_validations",
        schedule=crontab(minute="*/5"),
        args=(),
        kwargs={},
        queue="storage.provider-validation",
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="storage_schedule_repository_maintenance",
        task="apps.storage.tasks.schedule_repository_maintenance",
        schedule=schedule(settings.scan_interval),
        args=(),
        kwargs={},
        queue=None,
        enabled=settings.enabled,
    )
    TASK_REGISTRY.add(
        name="storage_reconcile_repositories",
        task="apps.storage.tasks.reconcile_storage_repositories",
        schedule=crontab(minute="*/15"),
        args=(),
        # Every run owns a new logical 15-minute history slot. Do not reuse the
        # interactive refresh staleness gate, which is based on completion time.
        kwargs={"limit": 200, "force": True, "stale_after_seconds": None},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="storage_reconcile_repository_operations",
        task="apps.storage.tasks.reconcile_repository_operations",
        schedule=schedule(settings.scan_interval),
        args=(),
        kwargs={"limit": 100},
        queue=None,
        enabled=True,
    )
    TASK_REGISTRY.add(
        name="storage_dispatch_repository_health_checks",
        task="apps.storage.tasks.dispatch_repository_health_checks",
        schedule=schedule(repository_health_interval_seconds()),
        args=(),
        kwargs={},
        queue=None,
        enabled=True,
    )
