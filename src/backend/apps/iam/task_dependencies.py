"""Read scope for task, node-command and external blocking conditions."""

from collections import defaultdict
from typing import Any, Iterable
from uuid import UUID

from apps.iam.query_authorization import request_authorization


def task_dependency_projection(
    request: Any, tasks: Iterable[Any],
) -> dict[int, list[tuple[Any, bool]]] | None:
    """Batch authorized dependencies, marking unsafe descriptions for redaction.

    A NodeTask may have no product Task. Owning its execution node permits a
    generic waiting reason, never another user's raw command identity/detail.
    External conditions without a linked task remain part of the visible
    owning task; its detail projection independently protects its dependencies.
    """
    context = request_authorization(request)
    if context is None or getattr(request, "hfl_support_readonly", False):
        return None
    if not context.valid:
        return {}
    from apps.iam.resource_access import filter_task_queryset
    from apps.iam.task_projection import filter_task_detail_queryset
    from apps.node.models import Node, NodeTask
    from apps.task.models import Task, TaskDependency

    rows = [row for task in tasks for row in task.dependencies.all()]
    if not rows:
        return {}
    node_refs = {}
    for row in rows:
        if row.blocking_task_id or row.reference_type != TaskDependency.ReferenceType.NODE_TASK:
            continue
        try:
            node_refs[row.pk] = UUID(row.reference_id)
        except (ValueError, TypeError, AttributeError):
            continue
    nodes = context.filter_task_resource_queryset(
        Node.all_objects.all(), "node", action="operations.tasks.view",
    ).order_by().values("pk")
    commands = {
        command.pk: command
        for command in NodeTask.objects.filter(
            organization_id=context.org.pk, pk__in=node_refs.values(),
            node_id__in=nodes,
        ).only("pk", "parent_task_id")
    }
    ids = {row.blocking_task_id for row in rows if row.blocking_task_id}
    ids.update(command.parent_task_id for command in commands.values() if command.parent_task_id)
    readable = filter_task_queryset(request, Task.objects.filter(pk__in=ids))
    allowed = set(readable.values_list("pk", flat=True))
    raw = set(filter_task_detail_queryset(
        readable, context, action="operations.tasks.view",
    ).values_list("pk", flat=True))
    result = defaultdict(list)
    for row in rows:
        if row.blocking_task_id:
            if row.blocking_task_id in allowed:
                result[row.task_id].append((row, row.blocking_task_id not in raw))
        elif row.reference_type == TaskDependency.ReferenceType.EXTERNAL:
            result[row.task_id].append((row, bool(row.reference_id)))
        elif row.reference_type == TaskDependency.ReferenceType.NODE_TASK:
            command = commands.get(node_refs.get(row.pk))
            if command is None:
                continue
            if command.parent_task_id is None:
                result[row.task_id].append((row, True))
            elif command.parent_task_id in allowed:
                result[row.task_id].append((row, command.parent_task_id not in raw))
    return result
