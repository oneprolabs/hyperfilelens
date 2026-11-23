"""Active backup/restore workload guards for node lifecycle operations."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.selectors.internal.node_task_query import active_node_tasks_for_node, node_tasks_queryset
from apps.node.services.internal.task_offline_reconcile import (
    product_task_blocks_cleanup,
)
from apps.task.constants import RESTORE_TASK_TYPES
from apps.task.models import Task, TaskResource

_ACTIVE_TASK_STATUSES = frozenset({Task.Status.PENDING, Task.Status.RUNNING})
_LIFECYCLE_NODE_TASK_KINDS = frozenset({"agent.upgrade", "agent.uninstall"})


@dataclass(frozen=True)
class NodeWorkloadBlocker:
    code: str
    task_uuid: str
    task_type: str
    label: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "task_uuid": self.task_uuid,
            "task_type": self.task_type,
            "label": self.label,
        }


def _task_label(task: Task) -> str:
    name = str(task.display_name or "").strip()
    task_type = str(task.task_type or "").strip()
    if name:
        return f"{task_type} · {name}" if task_type else name
    return task_type or str(task.task_uuid)


def _backup_tasks_for_agent(*, organization_id: int, node_id: int):
    by_payload = Task.objects.filter(
        organization_id=organization_id,
        task_type=Task.Type.BACKUP,
        status__in=_ACTIVE_TASK_STATUSES,
    ).order_by("-created_at", "-id")
    for task in by_payload:
        payload = task.request_payload if isinstance(task.request_payload, dict) else {}
        if (
            str(payload.get("source_type") or "") == "agent"
            and int(payload.get("source_ref_id") or 0) == node_id
        ):
            yield task

    yield from (
        Task.objects.filter(
            organization_id=organization_id,
            task_type=Task.Type.BACKUP,
            status__in=_ACTIVE_TASK_STATUSES,
            resources__resource_type=TaskResource.Type.BACKUP_SOURCE,
            resources__resource_subtype="agent",
            resources__resource_id=node_id,
        )
        .order_by("-created_at", "-id")
        .distinct()
    )


def _restore_tasks_for_execution_node(*, organization_id: int | None, node_id: int):
    from apps.restore.models import RestoreRecord

    task_ids = RestoreRecord.objects.filter(
        target_execution_organization_id=organization_id,
        target_execution_node_id=node_id,
    ).values_list("task_id", flat=True)
    yield from Task.objects.filter(
        pk__in=task_ids,
        task_type__in=RESTORE_TASK_TYPES,
        status__in=_ACTIVE_TASK_STATUSES,
    ).order_by("-created_at", "-id")


def _backup_tasks_for_proxy(*, organization_id: int, node_id: int):
    from apps.storage.repositories.models import Repository

    repo_ids = list(
        Repository.objects.filter(
            organization_id=organization_id,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=node_id,
        )
        .exclude(status=Repository.Status.REMOVED)
        .values_list("id", flat=True)
    )
    if not repo_ids:
        return
    yield from (
        Task.objects.filter(
            organization_id=organization_id,
            task_type=Task.Type.BACKUP,
            status__in=_ACTIVE_TASK_STATUSES,
            resources__resource_type=TaskResource.Type.REPOSITORY,
            resources__resource_id__in=repo_ids,
        )
        .order_by("-created_at", "-id")
        .distinct()
    )


def _restore_tasks_for_proxy_repository(*, organization_id: int, node_id: int):
    """Return active restores reading through repositories bound to a Proxy."""
    from apps.restore.models import RestoreRecordItem
    from apps.storage.repositories.models import Repository

    repository_ids = (
        Repository.objects.filter(
            organization_id=organization_id,
            bind_node_type=Repository.BindNodeType.PROXY,
            bind_node_id=node_id,
        )
        .exclude(status=Repository.Status.REMOVED)
        .values("id")
    )
    task_ids = RestoreRecordItem.objects.filter(
        organization_id=organization_id,
        repository_id__in=repository_ids,
    ).values("restore_record__task_id")
    yield from Task.objects.filter(
        organization_id=organization_id,
        pk__in=task_ids,
        task_type__in=RESTORE_TASK_TYPES,
        status__in=_ACTIVE_TASK_STATUSES,
    ).order_by("-created_at", "-id")


def _execution_node_tasks(*, org, node_id: int):
    for task in active_node_tasks_for_node(org=org, node=node_id):
        if task.kind in _LIFECYCLE_NODE_TASK_KINDS:
            continue
        yield task


def get_node_workload_blockers(*, node: Node) -> list[NodeWorkloadBlocker]:
    """Return active backup/restore (and execution) blockers for lifecycle ops."""
    org = node.organization
    organization_id = node.organization_id
    node_id = node.id
    blockers: list[NodeWorkloadBlocker] = []
    seen: set[str] = set()

    def add_task_blocker(*, task: Task, code: str) -> None:
        key = str(task.task_uuid)
        if key in seen:
            return
        seen.add(key)
        blockers.append(
            NodeWorkloadBlocker(
                code=code,
                task_uuid=key,
                task_type=str(task.task_type or ""),
                label=_task_label(task),
            )
        )

    if node.role == NodeRole.AGENT:
        for task in _backup_tasks_for_agent(
            organization_id=organization_id, node_id=node_id
        ):
            if product_task_blocks_cleanup(task=task):
                add_task_blocker(task=task, code="backup_running")
        for task in _restore_tasks_for_execution_node(
            organization_id=organization_id,
            node_id=node_id,
        ):
            if product_task_blocks_cleanup(task=task):
                add_task_blocker(task=task, code="restore_running")
    elif node.role == NodeRole.PROXY:
        for task in _backup_tasks_for_proxy(
            organization_id=organization_id, node_id=node_id
        ):
            if product_task_blocks_cleanup(task=task):
                add_task_blocker(task=task, code="backup_running")
        for task in _restore_tasks_for_execution_node(
            organization_id=organization_id,
            node_id=node_id,
        ):
            if product_task_blocks_cleanup(task=task):
                add_task_blocker(task=task, code="restore_running")
        for task in _restore_tasks_for_proxy_repository(
            organization_id=organization_id,
            node_id=node_id,
        ):
            add_task_blocker(task=task, code="restore_running")
    elif node.role == NodeRole.GATEWAY:
        for task in _restore_tasks_for_execution_node(
            organization_id=organization_id,
            node_id=node_id,
        ):
            if product_task_blocks_cleanup(task=task):
                add_task_blocker(task=task, code="restore_running")

    for node_task in _execution_node_tasks(org=org, node_id=node_id):
        blockers.append(
            NodeWorkloadBlocker(
                code="node_task_running",
                task_uuid=str(node_task.id),
                task_type=str(node_task.kind or ""),
                label=str(node_task.kind or "node_task"),
            )
        )

    return blockers


def get_node_remove_blockers(*, node: Node) -> list[NodeWorkloadBlocker]:
    """Return workload and configuration blockers specific to node removal."""
    blockers = get_node_workload_blockers(node=node)
    if node.role != NodeRole.GATEWAY:
        return blockers

    from apps.lens_bridge.models import LensKnowledgeSource, LensWorkspaceBinding

    knowledge_sources = LensKnowledgeSource.objects.filter(
        gateway_id=node.id,
        is_deleted=False,
    ).order_by("id")
    blockers.extend(
        NodeWorkloadBlocker(
            code="knowledge_source_bound",
            task_uuid=f"knowledge_source:{source.id}",
            task_type="lens_knowledge_source",
            label=f"Knowledge Source · {source.name}",
        )
        for source in knowledge_sources
    )
    active_bindings = LensWorkspaceBinding.objects.filter(
        execution_node_id=node.id,
        is_deleted=False,
    ).exclude(state=LensWorkspaceBinding.State.DELETED)
    blockers.extend(
        NodeWorkloadBlocker(
            code="workspace_cleanup_pending",
            task_uuid=f"workspace_binding:{binding.id}",
            task_type="lens_workspace_binding",
            label=f"Managed Workspace · {binding.workspace_uid}",
        )
        for binding in active_bindings.order_by("id")
    )
    return blockers


def node_workload_payload(*, node: Node) -> dict[str, Any]:
    return _workload_payload(get_node_workload_blockers(node=node))


def _workload_payload(reasons: list[NodeWorkloadBlocker]) -> dict[str, Any]:
    return {
        "blocked": bool(reasons),
        "reasons": [item.to_payload() for item in reasons],
    }


def agent_workloads_for_list(*, organization_id: int, node_ids: list[int]) -> dict[int, dict[str, Any]]:
    from apps.restore.models import RestoreRecord

    selected = set(node_ids)
    blockers: dict[int, list[NodeWorkloadBlocker]] = defaultdict(list)
    seen: dict[int, set[int]] = defaultdict(set)
    blocking: dict[int, bool] = {}

    def add_product_task(node_id: int, task: Task, code: str) -> None:
        if task.pk in seen[node_id]:
            return
        seen[node_id].add(task.pk)
        if task.pk not in blocking:
            blocking[task.pk] = product_task_blocks_cleanup(task=task)
        if blocking[task.pk]:
            blockers[node_id].append(NodeWorkloadBlocker(code, str(task.task_uuid), str(task.task_type or ""), _task_label(task)))

    backups = Task.objects.filter(
        organization_id=organization_id, task_type=Task.Type.BACKUP, status__in=_ACTIVE_TASK_STATUSES,
    )
    for task in backups.filter(request_payload__source_type="agent").order_by("-created_at", "-id"):
        try:
            ref_id = int(task.request_payload.get("source_ref_id") or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if ref_id in selected:
            add_product_task(ref_id, task, "backup_running")
    links = TaskResource.objects.filter(
        task__in=backups, resource_type=TaskResource.Type.BACKUP_SOURCE,
        resource_subtype="agent", resource_id__in=selected,
    ).select_related("task").order_by("-task__created_at", "-task_id")
    for link in links:
        add_product_task(link.resource_id, link.task, "backup_running")

    active_restores = Task.objects.filter(task_type__in=RESTORE_TASK_TYPES, status__in=_ACTIVE_TASK_STATUSES)
    restore_nodes: dict[int, set[int]] = defaultdict(set)
    for task_id, node_id in RestoreRecord.objects.filter(
        target_execution_organization_id=organization_id, target_execution_node_id__in=selected,
        task_id__in=active_restores.order_by().values("pk"),
    ).order_by().values_list("task_id", "target_execution_node_id"):
        restore_nodes[task_id].add(node_id)
    restores = active_restores.filter(pk__in=restore_nodes).order_by("-created_at", "-id")
    for task in restores:
        for node_id in restore_nodes[task.pk]:
            add_product_task(node_id, task, "restore_running")

    execution_tasks = node_tasks_queryset(organization_id=organization_id, active_only=True).filter(
        node_id__in=selected,
    ).exclude(kind__in=_LIFECYCLE_NODE_TASK_KINDS)
    for task in execution_tasks:
        blockers[task.node_id].append(NodeWorkloadBlocker(
            "node_task_running", str(task.id), str(task.kind or ""), str(task.kind or "node_task"),
        ))
    return {node_id: _workload_payload(blockers[node_id]) for node_id in node_ids}


def assert_node_available_for_lifecycle(*, node: Node) -> None:
    from apps.node.exceptions import NodeLifecycleError

    reasons = get_node_workload_blockers(node=node)
    if reasons:
        raise NodeLifecycleError(
            "Node has active backup, restore, or execution tasks.",
            code="node_workload_active",
            blockers=[item.to_payload() for item in reasons],
        )


def assert_node_available_for_removal(*, node: Node) -> None:
    """Reject removal while runtime or configuration dependencies remain."""
    from apps.node.exceptions import NodeLifecycleError

    reasons = get_node_remove_blockers(node=node)
    if reasons:
        raise NodeLifecycleError(
            "Node has active tasks or configured dependencies.",
            code="node_remove_blocked",
            blockers=[item.to_payload() for item in reasons],
        )
