"""Resource picker options for alert policies."""

from __future__ import annotations

from apps.alert.constants import ResourceType
from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.storage.repositories.models import Repository
from apps.task.models import Task


def _option(item_id, name: str, status: str = "active") -> dict:
    return {"id": str(item_id), "name": name, "status": status}


def resource_options(*, organization_id: int, resource_type: str | None) -> list[dict]:
    if not resource_type:
        return []
    if resource_type == ResourceType.SYSTEM:
        return [
            {
                "id": "00000000-0000-0000-0000-000000000000",
                "name": "Control Plane",
                "status": "active",
            }
        ]
    if resource_type == ResourceType.SYNC_PROXY:
        qs = Node.objects.filter(
            organization_id=organization_id, role=NodeRole.PROXY
        )
        return [_option(n.id, n.name, n.status) for n in qs.order_by("name")[:300]]
    if resource_type == ResourceType.AGENT_PROXY:
        qs = Node.objects.filter(
            organization_id=organization_id, role=NodeRole.AGENT
        )
        return [_option(n.id, n.name, n.status) for n in qs.order_by("name")[:300]]
    if resource_type == ResourceType.GATEWAY:
        qs = Node.objects.filter(
            organization_id=organization_id, role=NodeRole.GATEWAY
        )
        return [_option(n.id, n.name, n.status) for n in qs.order_by("name")[:300]]
    if resource_type in (
        ResourceType.BACKUP_REPOSITORY,
        ResourceType.TARGET_STORAGE,
    ):
        qs = Repository.objects.filter(organization_id=organization_id)
        return [
            _option(r.id, r.name, getattr(r, "status", "active") or "active")
            for r in qs.order_by("name")[:300]
        ]
    if resource_type == ResourceType.TASK:
        qs = Task.objects.filter(organization_id=organization_id)
        return [
            _option(j.task_uuid, f"{j.task_type} / {j.display_name}", j.status)
            for j in qs.order_by("-created_at", "-id")[:300]
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
            for u in User.objects.order_by("email")[:300]
        ]
    if resource_type == ResourceType.SOURCE_RESOURCE:
        try:
            from apps.source.models import SourceResource

            return [
                _option(r.id, r.name, r.status)
                for r in SourceResource.objects.filter(organization_id=organization_id).order_by(
                    "name"
                )[:300]
            ]
        except Exception:
            return []
    return []


def organization_resource_options(org: Organization, resource_type: str | None) -> list[dict]:
    return resource_options(organization_id=org.id, resource_type=resource_type)


def selected_resource_options(
    *, organization_id: int, resource_type: str | None, resource_ids: list
) -> list[dict]:
    """Resolve persisted policy resource IDs without changing their order."""
    ids = [str(value) for value in (resource_ids or [])]
    if resource_type == ResourceType.SYSTEM:
        return resource_options(
            organization_id=organization_id,
            resource_type=resource_type,
        )
    if not ids:
        return []

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
        rows = [
            _option(node.id, node.name, node.status)
            for node in Node.objects.filter(
                organization_id=organization_id,
                role=role_by_type[resource_type],
                id__in=ids,
            )
        ]
    elif resource_type in {
        ResourceType.BACKUP_REPOSITORY,
        ResourceType.TARGET_STORAGE,
    }:
        rows = [
            _option(repo.id, repo.name, repo.status)
            for repo in Repository.objects.filter(
                organization_id=organization_id,
                id__in=ids,
            )
        ]
    elif resource_type == ResourceType.TASK:
        rows = [
            _option(task.task_uuid, f"{task.task_type} / {task.display_name}", task.status)
            for task in Task.objects.filter(
                organization_id=organization_id,
                task_uuid__in=ids,
            )
        ]
    elif resource_type == ResourceType.SOURCE_RESOURCE:
        try:
            from apps.source.models import SourceResource

            rows = [
                _option(resource.id, resource.name, resource.status)
                for resource in SourceResource.objects.filter(
                    organization_id=organization_id,
                    id__in=ids,
                )
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
