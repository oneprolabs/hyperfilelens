"""Keep the source Pipeline projection synchronized with task state."""

from django.db import transaction
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.node.models import Node, NodeTask
from apps.node.models.base import NodeRole
from apps.source.constants import SelectableSourceKind
from apps.source.services.internal.source_pipeline import sync_task_pipeline_projection
from apps.source.services.internal.source_pipeline import (
    delete_pipeline_entry,
    sync_bound_proxy_pipeline_projections,
    sync_pipeline_projection,
)
from apps.task.signals import task_updated


_TERMINAL_NODE_TASK_STATUSES = {
    NodeTask.Status.SUCCESS,
    NodeTask.Status.FAILED,
    NodeTask.Status.TIMEOUT,
    NodeTask.Status.CANCELED,
}


def _queue_unregister_rechecks(task_ids) -> None:
    from apps.source.tasks.source_unregister import (
        reevaluate_source_unregister_task_task,
    )

    for task_id in task_ids:
        reevaluate_source_unregister_task_task.delay(task_id=int(task_id))


@receiver(task_updated)
def sync_source_pipeline_task(sender, task_uuid, **kwargs) -> None:
    from apps.task.models import Task, TaskDependency

    task_id = Task.objects.filter(task_uuid=task_uuid).values_list("id", flat=True).first()
    if task_id is not None:
        sync_task_pipeline_projection(task_id=int(task_id))
    if kwargs.get("status") == Task.Status.CANCELLED:
        task = Task.objects.filter(
            task_uuid=task_uuid,
            task_type=Task.Type.SOURCE_UNREGISTER,
        ).first()
        if task is not None:
            from apps.source.constants import ResourceStatus
            from apps.source.models import SourceResource
            from apps.task.models import TaskResource

            nas_ids = task.resources.filter(
                resource_type=TaskResource.Type.BACKUP_SOURCE,
                resource_subtype="nas",
            ).values_list("resource_id", flat=True)
            SourceResource.all_objects.filter(
                organization_id=task.organization_id,
                id__in=nas_ids,
                is_deleted=False,
                status=ResourceStatus.REMOVING,
            ).update(
                status=ResourceStatus.ACTIVE,
                status_message="Source deregistration was cancelled before cleanup started.",
            )
    if kwargs.get("status") not in {
        Task.Status.SUCCESS,
        Task.Status.FAILED,
        Task.Status.CANCELLED,
        Task.Status.TIMEOUT,
    }:
        return
    dependent_task_ids = tuple(
        TaskDependency.objects.filter(
            blocking_task__task_uuid=task_uuid,
            is_active=True,
            task__status__in={Task.Status.WAITING, Task.Status.BLOCKED},
        )
        .values_list("task_id", flat=True)
        .distinct()
    )
    if not dependent_task_ids:
        return
    transaction.on_commit(
        lambda task_ids=dependent_task_ids: _queue_unregister_rechecks(task_ids)
    )


@receiver(post_save, sender=NodeTask)
def recheck_source_unregister_after_node_task(
    sender,
    instance: NodeTask,
    **kwargs,
) -> None:
    """Wake exact deferred deregistrations when an Agent command terminates."""
    if instance.status not in _TERMINAL_NODE_TASK_STATUSES:
        return
    from apps.task.models import Task, TaskDependency

    dependency_match = Q(
        reference_type=TaskDependency.ReferenceType.NODE_TASK,
        reference_id=str(instance.id),
    )
    if instance.parent_task_id:
        dependency_match |= Q(blocking_task_id=instance.parent_task_id)
    dependent_task_ids = tuple(
        TaskDependency.objects.filter(
            dependency_match,
            is_active=True,
            task__status__in={Task.Status.WAITING, Task.Status.BLOCKED},
        )
        .values_list("task_id", flat=True)
        .distinct()
    )
    if dependent_task_ids:
        transaction.on_commit(
            lambda task_ids=dependent_task_ids: _queue_unregister_rechecks(task_ids)
        )


@receiver(post_save, sender=Node)
def sync_node_pipeline_projection(sender, instance: Node, created: bool, **kwargs) -> None:
    if instance.role == NodeRole.AGENT:
        # Enrollment owns initial Agent projection through sync_agent_source_host.
        if created:
            return
        if instance.is_deleted:
            delete_pipeline_entry(
                organization_id=instance.organization_id,
                source_kind=SelectableSourceKind.AGENT,
                ref_id=instance.id,
            )
        else:
            sync_pipeline_projection(
                organization_id=instance.organization_id,
                source_kind=SelectableSourceKind.AGENT,
                ref_id=instance.id,
            )
    elif instance.role == NodeRole.PROXY:
        sync_bound_proxy_pipeline_projections(proxy_id=instance.id)
