"""
Node read-path facade — other apps must import this module only.

Examples::

    from apps.node.selectors.interface import get_node, list_nodes, get_node_task

    node = get_node(org_key="acme", node_id=12)
    tasks = list_node_tasks(org_key="acme", node_id=12, active_only=True)
"""

from __future__ import annotations

import uuid
from typing import Any

from django.db.models import QuerySet

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask, NodeToken
from apps.node.selectors.internal.node_query import (
    node_by_id,
    node_by_id_global,
    nodes_for_org,
    nodes_queryset,
)
from apps.node.selectors.internal.node_task_query import (
    active_node_tasks_for_node,
    latest_node_task_for_node,
    node_task_by_correlation,
    node_task_by_correlation_for_requesting_org as _node_task_by_correlation_for_requesting_org,
    node_task_by_id,
    node_task_by_id_for_requesting_org,
    node_task_by_id_global,
    node_tasks_for_org,
    node_tasks_queryset,
)
from apps.node.selectors.internal.node_token_query import (
    node_tokens_for_org,
    node_tokens_queryset,
)

# --- Node ---


def list_nodes(
    *,
    org_key: str | None = None,
    organization: Organization | None = None,
    role: str | None = None,
    status: str | None = None,
) -> QuerySet[Node]:
    if organization is not None:
        return nodes_for_org(org=organization).filter(
            **({"role": role} if role else {}),
            **({"status": status} if status else {}),
        )
    return nodes_queryset(org_key=org_key, role=role, status=status)


def get_node(
    *,
    org_key: str,
    node_id: int,
) -> Node | None:
    return node_by_id_global(node_id=node_id, org_key=org_key)


def get_node_for_org(*, org: Organization, node_id: int) -> Node | None:
    return node_by_id(org=org, node_id=node_id)


def node_display_name(*, node_id: int, organization_id: int | None = None) -> str:
    qs = Node.objects.filter(pk=node_id)
    if organization_id:
        qs = qs.filter(organization_id=organization_id)
    return str(qs.values_list("name", flat=True).first() or "").strip()


# --- NodeTask ---


def list_node_tasks(
    *,
    org_key: str | None = None,
    organization: Organization | None = None,
    node_id: int | None = None,
    status: str | None = None,
    kind: str | None = None,
    correlation_type: str | None = None,
    correlation_id: str | None = None,
    active_only: bool = False,
) -> QuerySet[NodeTask]:
    if organization is not None:
        qs = node_tasks_for_org(org=organization)
        if node_id is not None:
            qs = qs.filter(node_id=node_id)
        if status:
            qs = qs.filter(status=status)
        if kind:
            qs = qs.filter(kind=kind)
        if correlation_type:
            qs = qs.filter(correlation_type=correlation_type)
        if correlation_id:
            qs = qs.filter(correlation_id=correlation_id)
        if active_only:
            qs = qs.filter(
                status__in=(
                    NodeTask.Status.PENDING,
                    NodeTask.Status.RUNNING,
                ),
            )
        return qs
    return node_tasks_queryset(
        org_key=org_key,
        node_id=node_id,
        status=status,
        kind=kind,
        correlation_type=correlation_type,
        correlation_id=correlation_id,
        active_only=active_only,
    )


def get_node_task(
    *,
    org_key: str,
    task_id: uuid.UUID | str,
) -> NodeTask | None:
    return node_task_by_id_global(task_id=task_id, org_key=org_key)


def get_node_task_for_org(
    *,
    org: Organization,
    task_id: uuid.UUID | str,
) -> NodeTask | None:
    return node_task_by_id(org=org, task_id=task_id)


def get_node_task_for_requesting_org(
    *,
    org: Organization,
    task_id: uuid.UUID | str,
) -> NodeTask | None:
    return node_task_by_id_for_requesting_org(org=org, task_id=task_id)


def get_node_task_by_correlation(
    *,
    org: Organization,
    correlation_type: str,
    correlation_id: str,
    active_only: bool = False,
) -> NodeTask | None:
    return node_task_by_correlation(
        org=org,
        correlation_type=correlation_type,
        correlation_id=correlation_id,
        active_only=active_only,
    )


def get_node_task_by_correlation_for_requesting_org(
    *,
    org: Organization,
    correlation_type: str,
    correlation_id: str,
    active_only: bool = False,
) -> NodeTask | None:
    return _node_task_by_correlation_for_requesting_org(
        org=org,
        correlation_type=correlation_type,
        correlation_id=correlation_id,
        active_only=active_only,
    )


def list_active_node_tasks_for_node(
    *,
    org: Organization,
    node_id: int,
) -> QuerySet[NodeTask]:
    return active_node_tasks_for_node(org=org, node=node_id)


def get_latest_node_task_for_node(
    *,
    org: Organization,
    node_id: int,
    kind: str | None = None,
) -> NodeTask | None:
    return latest_node_task_for_node(org=org, node=node_id, kind=kind)


# --- NodeToken (enrollment) ---


def list_node_tokens(
    *,
    org_key: str | None = None,
    organization: Organization | None = None,
    role: str | None = None,
    is_active: bool | None = None,
) -> QuerySet[NodeToken]:
    if organization is not None:
        qs = node_tokens_for_org(org=organization)
        if role:
            qs = qs.filter(role=role)
        if is_active is not None:
            qs = qs.filter(is_active=is_active)
        return qs
    return node_tokens_queryset(org_key=org_key, role=role, is_active=is_active)


# --- Runtime (Redis) ---


def get_node_task_runtime_info(*, task_id: uuid.UUID | str) -> dict[str, Any] | None:
    """Hot-path task snapshot from Redis (not PostgreSQL)."""
    from apps.node.services.internal.redis_store import get_task_info

    return get_task_info(task_id=str(task_id))


__all__ = [
    "get_latest_node_task_for_node",
    "get_node",
    "get_node_for_org",
    "node_display_name",
    "get_node_task",
    "get_node_task_by_correlation",
    "get_node_task_by_correlation_for_requesting_org",
    "get_node_task_for_org",
    "get_node_task_for_requesting_org",
    "get_node_task_runtime_info",
    "list_active_node_tasks_for_node",
    "list_node_tasks",
    "list_node_tokens",
    "list_nodes",
]
