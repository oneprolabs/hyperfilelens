"""Internal read queries for ``NodeTask``."""

from __future__ import annotations

import uuid

from django.db.models import QuerySet

from apps.iam.models import Organization
from apps.node.models import Node, NodeTask

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


def node_tasks_for_org(*, org: Organization) -> QuerySet[NodeTask]:
    return node_tasks_queryset(organization_id=org.id)


def node_tasks_for_node(*, org: Organization, node: Node | int) -> QuerySet[NodeTask]:
    node_pk = node.pk if isinstance(node, Node) else node
    return node_tasks_queryset(organization_id=org.id, node_id=node_pk)


def active_node_tasks_for_node(
    *,
    org: Organization,
    node: Node | int,
) -> QuerySet[NodeTask]:
    node_pk = node.pk if isinstance(node, Node) else node
    return node_tasks_queryset(
        organization_id=org.id,
        node_id=node_pk,
        active_only=True,
    )


def node_task_by_id(
    *,
    org: Organization,
    task_id: uuid.UUID | str,
) -> NodeTask | None:
    return node_tasks_for_org(org=org).filter(pk=task_id).first()


def node_task_by_id_for_requesting_org(
    *,
    org: Organization,
    task_id: uuid.UUID | str,
) -> NodeTask | None:
    """Resolve a task through its tenant/requesting identity.

    Shared platform nodes execute under the platform organization, while the
    initiating tenant remains the only product-facing owner of the task.
    """

    return (
        node_tasks_queryset()
        .filter(requesting_organization_id=org.id, pk=task_id)
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
    org: Organization,
    correlation_type: str,
    correlation_id: str,
    active_only: bool = False,
) -> NodeTask | None:
    return (
        node_tasks_queryset(
            organization_id=org.id,
            correlation_type=correlation_type,
            correlation_id=correlation_id,
            active_only=active_only,
        )
        .first()
    )


def node_task_by_correlation_for_requesting_org(
    *,
    org: Organization,
    correlation_type: str,
    correlation_id: str,
    active_only: bool = False,
) -> NodeTask | None:
    queryset = node_tasks_queryset(
        correlation_type=correlation_type,
        correlation_id=correlation_id,
        active_only=active_only,
    ).filter(requesting_organization_id=org.id)
    return queryset.first()


def latest_node_task_for_node(
    *,
    org: Organization,
    node: Node | int,
    kind: str | None = None,
) -> NodeTask | None:
    qs = node_tasks_for_node(org=org, node=node)
    if kind:
        qs = qs.filter(kind=kind)
    return qs.first()
