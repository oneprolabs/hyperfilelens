"""Internal read queries for ``NodeTask``."""

from __future__ import annotations

import uuid

from django.db.models import QuerySet

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask
from apps.node.services.internal.ownership import owner_id

_ACTIVE_STATUSES = (NodeTask.Status.PENDING, NodeTask.Status.RUNNING)


def node_tasks_queryset(
    *,
    org_key: str | None = None,
    organization_id: int | None = None,
    node_id: int | None = None,
    status: str | None = None,
    kind: str | None = None,
    correlation_type: str | None = None,
    correlation_id: str | None = None,
    active_only: bool = False,
) -> QuerySet[NodeTask]:
    queryset = NodeTask.objects.select_related("organization", "node").all()
    if org_key:
        queryset = queryset.filter(organization__key=org_key)
    if organization_id is not None:
        queryset = queryset.filter(organization_id=organization_id)
    if node_id is not None:
        queryset = queryset.filter(node_id=node_id)
    if status:
        queryset = queryset.filter(status=status)
    if kind:
        queryset = queryset.filter(kind=kind)
    if correlation_type:
        queryset = queryset.filter(correlation_type=correlation_type)
    if correlation_id:
        queryset = queryset.filter(correlation_id=correlation_id)
    if active_only:
        queryset = queryset.filter(status__in=_ACTIVE_STATUSES)
    return queryset.order_by("-created_at", "-id")


def node_tasks_for_org(*, org: Organization | None) -> QuerySet[NodeTask]:
    return node_tasks_queryset().filter(organization_id=owner_id(org))


def node_tasks_for_node(*, org: Organization | None, node: Node | int) -> QuerySet[NodeTask]:
    node_pk = node.pk if isinstance(node, Node) else node
    return node_tasks_queryset(node_id=node_pk).filter(organization_id=owner_id(org))


def active_node_tasks_for_node(
    *,
    org: Organization | None,
    node: Node | int,
) -> QuerySet[NodeTask]:
    node_pk = node.pk if isinstance(node, Node) else node
    return node_tasks_queryset(
        organization_id=owner_id(org),
        node_id=node_pk,
        active_only=True,
    ).filter(organization_id=owner_id(org))


def node_task_by_id(
    *,
    org: Organization | None,
    task_id: uuid.UUID | str,
) -> NodeTask | None:
    return node_tasks_for_org(org=org).filter(pk=task_id).first()


def node_task_by_id_for_requesting_org(
    *,
    org: Organization | None,
    task_id: uuid.UUID | str,
) -> NodeTask | None:
    """Resolve a task through its tenant/requesting identity.

    Shared platform nodes execute under the platform organization, while the
    initiating tenant remains the only product-facing owner of the task.
    """

    if org is None:
        raise ValueError("A requesting tenant is required")
    return (
        node_tasks_queryset()
        .filter(requesting_organization_id=owner_id(org), pk=task_id)
        .first()
    )


def node_task_by_id_global(
    *,
    task_id: uuid.UUID | str,
    org_key: str | None = None,
) -> NodeTask | None:
    return node_tasks_queryset(org_key=org_key).filter(pk=task_id).first()


def node_task_by_correlation(
    *,
    org: Organization | None,
    correlation_type: str,
    correlation_id: str,
    active_only: bool = False,
) -> NodeTask | None:
    """Resolve an exact owner; ``None`` selects platform tasks, not all tasks."""
    return (
        node_tasks_queryset(
            correlation_type=correlation_type,
            correlation_id=correlation_id,
            active_only=active_only,
        )
        .filter(organization_id=owner_id(org))
        .first()
    )


def node_task_by_correlation_for_requesting_org(
    *,
    org: Organization | None,
    correlation_type: str,
    correlation_id: str,
    active_only: bool = False,
) -> NodeTask | None:
    if org is None:
        raise ValueError("A requesting tenant is required")
    queryset = node_tasks_queryset(
        correlation_type=correlation_type,
        correlation_id=correlation_id,
        active_only=active_only,
    ).filter(requesting_organization_id=owner_id(org))
    return queryset.first()


def latest_node_task_for_node(
    *,
    org: Organization | None,
    node: Node | int,
    kind: str | None = None,
) -> NodeTask | None:
    qs = node_tasks_for_node(org=org, node=node)
    if kind:
        qs = qs.filter(kind=kind)
    return qs.first()
