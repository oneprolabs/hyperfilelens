"""Celery tasks for system metric collection."""

from celery import shared_task

from common.observability.celery_context import logged_celery_task

from apps.monitor.services.events import cleanup_operational_events
from apps.monitor.services.interface import cleanup_old_metrics, collect_and_persist_sample
from apps.monitor.services.internal.resource_metrics import (
    cleanup_old_resource_metrics,
    snapshot_repository_metrics,
)
from apps.monitor.services.internal.repository_usage_history import (
    cleanup_repository_usage_history,
)


@shared_task(name="apps.monitor.tasks.metrics.collect_system_metrics")
@logged_celery_task(name="apps.monitor.tasks.metrics.collect_system_metrics")
def collect_system_metrics():
    collect_and_persist_sample()
    return {"status": "ok"}


@shared_task(name="apps.monitor.tasks.metrics.cleanup_old_system_metrics")
@logged_celery_task(name="apps.monitor.tasks.metrics.cleanup_old_system_metrics", trace_keys=("days_to_keep",))
def cleanup_old_system_metrics(days_to_keep: int = 7):
    deleted = cleanup_old_metrics(days_to_keep=days_to_keep)
    return {"deleted": deleted}


@shared_task(name="apps.monitor.tasks.metrics.snapshot_repository_metrics")
@logged_celery_task(name="apps.monitor.tasks.metrics.snapshot_repository_metrics")
def snapshot_repository_metrics_task():
    created = snapshot_repository_metrics()
    return {"created": created}


@shared_task(name="apps.monitor.tasks.metrics.cleanup_old_resource_metrics")
@logged_celery_task(
    name="apps.monitor.tasks.metrics.cleanup_old_resource_metrics",
    trace_keys=("days_to_keep",),
)
def cleanup_old_resource_metrics_task(days_to_keep: int = 14):
    deleted = cleanup_old_resource_metrics(days_to_keep=days_to_keep)
    return {"deleted": deleted}


@shared_task(name="apps.monitor.tasks.metrics.cleanup_repository_usage_history")
@logged_celery_task(
    name="apps.monitor.tasks.metrics.cleanup_repository_usage_history",
    trace_keys=("days_to_keep", "batch_size"),
)
def cleanup_repository_usage_history_task(
    days_to_keep: int = 30,
    batch_size: int = 2000,
):
    deleted = cleanup_repository_usage_history(
        days_to_keep=days_to_keep,
        batch_size=batch_size,
    )
    return {"deleted": deleted}


@shared_task(name="apps.monitor.tasks.metrics.cleanup_operational_events")
@logged_celery_task(
    name="apps.monitor.tasks.metrics.cleanup_operational_events",
    trace_keys=("days_to_keep", "batch_size"),
)
def cleanup_operational_events_task(
    days_to_keep: int = 90,
    batch_size: int = 2000,
):
    deleted = cleanup_operational_events(
        days_to_keep=days_to_keep,
        batch_size=batch_size,
    )
    return {"deleted": deleted}
