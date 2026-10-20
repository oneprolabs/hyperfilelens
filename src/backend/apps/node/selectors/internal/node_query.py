"""Internal read queries for ``Node``."""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.iam.models import Organization
from apps.node.models import Node
from apps.node.services.internal.ownership import node_owner_filter


def nodes_queryset(
    *,
    org_key: str | None = None,
    organization_id: int | None = None,
    role: str | None = None,
    status: str | None = None,
) -> QuerySet[Node]:
    queryset = Node.objects.select_related("organization").all()
    if org_key:
        queryset = queryset.filter(organization__key=org_key)
    if organization_id is not None:
        queryset = queryset.filter(organization_id=organization_id)
    if role:
        queryset = queryset.filter(role=role)
    if status:
        queryset = queryset.filter(status=status)
    return queryset.order_by("organization_id", "name", "id")


def nodes_for_org(*, org: Organization | None) -> QuerySet[Node]:
    return Node.objects.select_related("organization").filter(**node_owner_filter(org))


def node_by_id(*, org: Organization | None, node_id: int) -> Node | None:
    return nodes_for_org(org=org).filter(pk=node_id).first()


def node_by_id_global(
    *,
    node_id: int,
    org_key: str | None = None,
) -> Node | None:
    return nodes_queryset(org_key=org_key).filter(pk=node_id).first()


def node_search_q(term: str) -> Q:
    query = (
        Q(name__icontains=term)
        | Q(ip_address__icontains=term)
        | Q(os_name__icontains=term)
        | Q(version__icontains=term)
        | Q(metadata__hostname__icontains=term)
        | Q(metadata__inventory__hostname__icontains=term)
    )
    return query


def node_field_search_q(field: str, term: str) -> Q | None:
    normalized = (field or "").strip().lower()
    if normalized == "name":
        return Q(name__icontains=term)
    if normalized in {"ip", "ip_address"}:
        return Q(ip_address__icontains=term)
    return None
