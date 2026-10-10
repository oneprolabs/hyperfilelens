"""Fail-closed coordination shared by backup admission and orphan cleanup."""

from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models import CharField
from django.db.models.functions import Cast

from apps.node.models import NodeTask
from apps.storage.repositories.models import RepositoryExecutionTarget, RepositoryOrphanSnapshot
from apps.task.models import Task, TaskResource


ACTIVE = {Task.Status.PENDING, Task.Status.WAITING, Task.Status.BLOCKED, Task.Status.RUNNING}


def assert_no_orphan_cleanup(repository_id):
    """Caller holds the Repository row lock before accepting/mutating work."""
    if RepositoryExecutionTarget.objects.filter(
        repository_id=repository_id,
        active_task__repository_operation__operation_type="snapshot.reconcile",
    ).exists() or RepositoryOrphanSnapshot.objects.filter(
        target__repository_id=repository_id, status="deleting",
    ).exists():
        raise ValidationError({
            "repository_id": "Orphan snapshot reconciliation is active or awaiting execution confirmation."
        })


def backup_blocker(repository):
    tasks = Task.objects.filter(
        organization_id=repository.organization_id,
        task_type=Task.Type.BACKUP,
    ).filter(
        Q(request_payload__repository_id=repository.id)
        | Q(resources__resource_type=TaskResource.Type.REPOSITORY, resources__resource_id=repository.id)
    ).distinct()
    if tasks.filter(status__in=ACTIVE).exists():
        return "Backup execution or result registration is still active."
    children = NodeTask.objects.filter(
        organization_id=repository.organization_id,
    ).filter(
        Q(parent_task_id__in=tasks.values("id"))
        | Q(correlation_id__in=tasks.annotate(uuid_text=Cast("task_uuid", CharField())).values("uuid_text"))
    ).filter(kind__in=["backup.run", "backup.policy.prepare", "backup"])
    if children.filter(status__in=[NodeTask.Status.PENDING, NodeTask.Status.RUNNING]).exists():
        return "Agent backup execution is still active."
    for child in children.filter(status__in=[
        NodeTask.Status.TIMEOUT, NodeTask.Status.CANCELED, NodeTask.Status.FAILED,
    ]).iterator():
        result = child.result or {}
        # Controller terminal state alone cannot prove remote termination.
        if child.accepted_at and not result:
            return "Agent backup termination is unconfirmed."
        if child.status in {NodeTask.Status.TIMEOUT, NodeTask.Status.CANCELED} and not result.get("delivery_timeout_sealed"):
            return "Agent backup result is unknown."
    return ""
