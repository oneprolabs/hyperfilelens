"""Celery tasks for the source app."""

from .connection_probe import (
    probe_source_resource_capacity,
    queue_source_availability_probes_for_proxy,
    queue_source_availability_probes_for_proxy_task,
    queue_source_resource_capacity_probe,
    reconcile_stale_source_connection_probes_task,
    reconcile_source_availability_task,
)
from .source_unregister import (
    execute_source_unregister_task,
    reconcile_stuck_source_unregister_tasks_task,
    reevaluate_source_unregister_task_task,
)
from .pipeline import (
    queue_source_pipeline_projection,
    reconcile_source_pipeline_task,
    sync_source_pipeline_projection_task,
)

__all__ = [
    "execute_source_unregister_task",
    "probe_source_resource_capacity",
    "queue_source_availability_probes_for_proxy",
    "queue_source_availability_probes_for_proxy_task",
    "queue_source_resource_capacity_probe",
    "reconcile_stale_source_connection_probes_task",
    "reconcile_source_availability_task",
    "reconcile_source_pipeline_task",
    "sync_source_pipeline_projection_task",
    "queue_source_pipeline_projection",
    "reconcile_stuck_source_unregister_tasks_task",
    "reevaluate_source_unregister_task_task",
]
