"""Resource picker options for alert policies."""

from __future__ import annotations

from apps.alert.constants import ResourceType
from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.storage.repositories.models import Repository
from apps.task.models import Task


def _scoped(request, queryset, kind):
    if request is None:
        return queryset
    from apps.iam.resource_access import filter_resource_queryset, filter_task_queryset
    if kind == "task":
        from apps.iam.query_authorization import request_authorization
        from apps.iam.task_projection import filter_task_detail_queryset

        queryset = filter_task_queryset(request, queryset)
        if getattr(request, "hfl_support_readonly", False):
            return queryset
        context = request_authorization(request)
        if context is None:
            return queryset
        # Options and persisted selections disclose raw display names, not
        # merely the basic status projection available through Tasks.
        return filter_task_detail_queryset(
            queryset, context, action="operations.tasks.view",
        )
    return filter_resource_queryset(request, queryset, kind, action="resource.view")


def _scoped_users(request, queryset, organization_id):
    from apps.iam.models import Membership
    queryset = queryset.filter(pk__in=Membership.objects.filter(
        organization_id=organization_id, is_active=True,
    ).values("user_id"))
    if request is not None:
        from apps.iam.query_authorization import request_authorization
        context = request_authorization(request)
        if context is not None and not context.has_org_action("configuration.members.view"):
            return queryset.none()
    return queryset


def _option(item_id, name: str, status: str = "active") -> dict:
    return {"id": str(item_id), "name": name, "status": status}


def resource_options(
    *,
    organization_id: int | None,
    resource_type: str | None,
    owner_scope: str | None = None,
    request=None,
) -> list[dict]:
    from common.resource_scope import ResourceScope

    if not resource_type:
        return []
    if resource_type == ResourceType.SYSTEM:
        if request is not None and organization_id is not None:
            return []
        return [
            {
                "id": "00000000-0000-0000-0000-000000000000",
                "name": "Control Plane",
                "status": "active",
            }
        ]
    platform_owned = owner_scope == ResourceScope.PLATFORM or organization_id is None
    if resource_type == ResourceType.SYNC_PROXY:
        qs = Node.objects.filter(role=NodeRole.PROXY)
        qs = (
            qs.filter(scope=ResourceScope.PLATFORM, organization__isnull=True)
            if platform_owned
            else qs.filter(organization_id=organization_id)
        )
        return [_option(n.id, n.name, n.status) for n in _scoped(request, qs, "node").order_by("name")[:300]]
    if resource_type == ResourceType.AGENT_PROXY:
        qs = Node.objects.filter(role=NodeRole.AGENT)
        qs = (
            qs.filter(scope=ResourceScope.PLATFORM, organization__isnull=True)
            if platform_owned
            else qs.filter(organization_id=organization_id)
        )
        return [_option(n.id, n.name, n.status) for n in _scoped(request, qs, "node").order_by("name")[:300]]
    if resource_type == ResourceType.GATEWAY:
        qs = Node.objects.filter(role=NodeRole.GATEWAY)
        qs = (
            qs.filter(scope=ResourceScope.PLATFORM, organization__isnull=True)
            if platform_owned
            else qs.filter(organization_id=organization_id)
        )
        return [_option(n.id, n.name, n.status) for n in _scoped(request, qs, "node").order_by("name")[:300]]
    if platform_owned:
        # Platform policies do not target tenant repositories / tasks / sources.
        return []
    if resource_type in (
        ResourceType.BACKUP_REPOSITORY,
        ResourceType.TARGET_STORAGE,
    ):
        qs = Repository.objects.filter(organization_id=organization_id)
        return [
            _option(r.id, r.name, getattr(r, "status", "active") or "active")
            for r in _scoped(request, qs, "repository").order_by("name")[:300]
        ]
    if resource_type == ResourceType.TASK:
        qs = Task.objects.filter(organization_id=organization_id)
        return [
            _option(j.task_uuid, f"{j.task_type} / {j.display_name}", j.status)
            for j in _scoped(request, qs, "task").order_by("-created_at", "-id")[:300]
        ]
    if resource_type == ResourceType.USER:
        from django.contrib.auth import get_user_model

        User = get_user_model()
        return [
            _option(
                u.id,
                getattr(u, "email", None) or str(u.id),
                "active" if u.is_active else "inactive",
            )
            for u in _scoped_users(request, User.objects.all(), organization_id).order_by("email")[:300]
        ]
    if resource_type == ResourceType.SOURCE_RESOURCE:
        try:
            from apps.source.models import SourceResource

            return [
                _option(r.id, r.name, r.status)
                for r in _scoped(request, SourceResource.objects.filter(organization_id=organization_id), "source_resource").order_by("name")[:300]
            ]
        except Exception:
            return []
    return []


def organization_resource_options(org: Organization, resource_type: str | None, *, request=None) -> list[dict]:
    return resource_options(organization_id=org.id, resource_type=resource_type, request=request)


def selected_resource_options(
    *,
    organization_id: int | None,
    resource_type: str | None,
    resource_ids: list,
    owner_scope: str | None = None,
    request=None,
) -> list[dict]:
    """Resolve persisted policy resource IDs without changing their order."""
    from common.resource_scope import ResourceScope

    ids = [str(value) for value in (resource_ids or [])]
    if resource_type == ResourceType.SYSTEM:
        return resource_options(
            organization_id=organization_id,
            resource_type=resource_type,
            owner_scope=owner_scope,
            request=request,
        )
    if not ids:
        return []

    platform_owned = owner_scope == ResourceScope.PLATFORM or organization_id is None
    rows: list[dict] = []
    if resource_type in {
        ResourceType.SYNC_PROXY,
        ResourceType.AGENT_PROXY,
        ResourceType.GATEWAY,
    }:
        role_by_type = {
            ResourceType.SYNC_PROXY: NodeRole.PROXY,
            ResourceType.AGENT_PROXY: NodeRole.AGENT,
            ResourceType.GATEWAY: NodeRole.GATEWAY,
        }
        node_qs = Node.objects.filter(
            role=role_by_type[resource_type],
            id__in=ids,
        )
        if platform_owned:
            node_qs = node_qs.filter(scope=ResourceScope.PLATFORM, organization__isnull=True)
        else:
            node_qs = node_qs.filter(organization_id=organization_id)
        rows = [
            _option(node.id, node.name, node.status)
            for node in _scoped(request, node_qs, "node")
        ]
    elif platform_owned:
        rows = []
    elif resource_type in {
        ResourceType.BACKUP_REPOSITORY,
        ResourceType.TARGET_STORAGE,
    }:
        rows = [
            _option(repo.id, repo.name, repo.status)
            for repo in _scoped(request, Repository.objects.filter(
                organization_id=organization_id, id__in=ids,
            ), "repository")
        ]
    elif resource_type == ResourceType.TASK:
        rows = [
            _option(task.task_uuid, f"{task.task_type} / {task.display_name}", task.status)
            for task in _scoped(request, Task.objects.filter(
                organization_id=organization_id, task_uuid__in=ids,
            ), "task")
        ]
    elif resource_type == ResourceType.SOURCE_RESOURCE:
        try:
            from apps.source.models import SourceResource

            rows = [
                _option(resource.id, resource.name, resource.status)
                for resource in _scoped(request, SourceResource.objects.filter(
                    organization_id=organization_id, id__in=ids,
                ), "source_resource")
            ]
        except Exception:
            rows = []

    by_id = {str(row["id"]): row for row in rows}
    return [
        by_id.get(
            resource_id,
            {
                "id": resource_id,
                "name": "",
                "status": "unavailable",
                "available": False,
            },
        )
        for resource_id in ids
    ]
