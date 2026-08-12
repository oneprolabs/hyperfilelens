"""Keep the source Pipeline projection synchronized with task state."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.source.constants import SelectableSourceKind
from apps.source.services.internal.source_pipeline import sync_task_pipeline_projection
from apps.source.services.internal.source_pipeline import (
    delete_pipeline_entry,
    sync_bound_proxy_pipeline_projections,
    sync_pipeline_projection,
)
from apps.task.signals import task_updated


@receiver(task_updated)
def sync_source_pipeline_task(sender, task_uuid, **kwargs) -> None:
    from apps.task.models import Task

    task_id = Task.objects.filter(task_uuid=task_uuid).values_list("id", flat=True).first()
    if task_id is not None:
        sync_task_pipeline_projection(task_id=int(task_id))


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
