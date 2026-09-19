"""Resource visibility helpers shared by Host business endpoints."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from rest_framework.exceptions import PermissionDenied

from apps.iam.permissions_org import resolve_org_key
from common.extension_spi import get_authz_provider


def visible_resource_refs(
    request: Any,
    refs: list[tuple[str, int]] | tuple[tuple[str, int], ...],
    *,
    action: str = "resources.view",
) -> set[tuple[str, int]] | None:
    """Return allowed refs, or ``None`` when Community has no EE filter."""
    provider = get_authz_provider()
    if provider is None:
        return None
    visible_resource_refs_fn = getattr(provider, "visible_resource_refs", None)
    if visible_resource_refs_fn is None:
        return None
    org_key = resolve_org_key(request)
    if not org_key:
        return set()
    normalized = tuple((str(resource_type), int(resource_id)) for resource_type, resource_id in refs)
    if not normalized:
        return set()
    return {
        (str(resource_type), int(resource_id))
        for resource_type, resource_id in visible_resource_refs_fn(
            request.user,
            org_key,
            normalized,
            action=action,
        )
    }


def filter_resource_queryset(
    request: Any,
    queryset: QuerySet,
    resource_type: str,
    *,
    action: str = "resources.view",
) -> QuerySet:
    """Filter an organization queryset through the optional EE provider.

    Community has no provider and keeps its existing organization-level
    behavior. Enterprise returns only resource IDs allowed by the user's
    role and resource scope. An authenticated Enterprise request without an
    organization context fails closed.
    """
    resource_ids = list(queryset.values_list("pk", flat=True))
    if not resource_ids:
        return queryset
    visible_refs = visible_resource_refs(
        request,
        [(resource_type, int(resource_id)) for resource_id in resource_ids],
        action=action,
    )
    if visible_refs is None:
        return queryset
    visible_ids = [resource_id for resource_type_key, resource_id in visible_refs
                   if resource_type_key == resource_type]
    return queryset.filter(pk__in=visible_ids)


def filter_queryset_by_resource_field(
    request: Any,
    queryset: QuerySet,
    resource_type: str,
    resource_id_field: str,
    *,
    action: str = "resources.view",
) -> QuerySet:
    """Filter derived rows through the owner of a referenced Host resource."""
    resource_ids = list(
        queryset.values_list(resource_id_field, flat=True).distinct()
    )
    visible = visible_resource_refs(
        request,
        [(resource_type, int(resource_id)) for resource_id in resource_ids if resource_id],
        action=action,
    )
    if visible is None:
        return queryset
    allowed_ids = {
        resource_id
        for visible_type, resource_id in visible
        if visible_type == resource_type
    }
    return queryset.filter(**{f"{resource_id_field}__in": allowed_ids})


def record_resource_owner(
    *,
    organization_id: int,
    resource_type: str,
    resource_id: int,
    owner_id: int | None,
) -> None:
    """Forward Host resource ownership to EE when the extension is active."""
    provider = get_authz_provider()
    if provider is None:
        return
    record_owner = getattr(provider, "record_resource_owner", None)
    if record_owner is None:
        return
    record_owner(
        organization_id=organization_id,
        resource_type=resource_type,
        resource_id=int(resource_id),
        owner_id=owner_id,
    )


def get_resource_owner_id(
    *,
    organization_id: int,
    resource_type: str,
    resource_id: int,
) -> int | None:
    """Return a resource owner through the optional Enterprise provider."""
    provider = get_authz_provider()
    if provider is None:
        return None
    resolve_owner = getattr(provider, "get_resource_owner_id", None)
    if resolve_owner is None:
        return None
    return resolve_owner(
        organization_id=organization_id,
        resource_type=resource_type,
        resource_id=int(resource_id),
    )


def assert_resource_access(
    request: Any,
    resource_type: str,
    resource_id: int,
    *,
    action: str = "resources.view",
) -> None:
    """Raise a uniform 403 when EE denies one resource reference."""
    visible = visible_resource_refs(
        request,
        ((resource_type, int(resource_id)),),
        action=action,
    )
    if visible is None:
        return
    if (resource_type, int(resource_id)) not in visible:
        raise PermissionDenied("You do not have access to this resource.")


def filter_task_queryset(request: Any, queryset: QuerySet) -> QuerySet:
    """Restrict task rows to resources visible to the current EE member."""
    visible = visible_resource_refs
    provider = get_authz_provider()
    if provider is None or getattr(provider, "visible_resource_refs", None) is None:
        return queryset

    from apps.task.models import TaskResource

    task_ids = list(queryset.values_list("id", flat=True))
    if not task_ids:
        return queryset
    resource_rows = list(
        TaskResource.objects.filter(task_id__in=task_ids).values(
            "task_id", "resource_type", "resource_subtype", "resource_id"
        )
    )
    refs_by_task: dict[int, list[tuple[str, int]]] = {}
    refs: list[tuple[str, int]] = []
    for row in resource_rows:
        resource_type = str(row["resource_type"] or "")
        subtype = str(row["resource_subtype"] or "")
        if resource_type == "backup_source":
            if subtype == "agent":
                mapped_type = "node"
            elif subtype == "nas":
                mapped_type = "source_resource"
            else:
                continue
        elif resource_type in {"backup_config", "repository", "target_repository"}:
            mapped_type = "repository" if resource_type != "backup_config" else resource_type
        elif resource_type == "host":
            mapped_type = "node"
        else:
            continue
        ref = (mapped_type, int(row["resource_id"]))
        refs.append(ref)
        refs_by_task.setdefault(int(row["task_id"]), []).append(ref)
    task_payloads = queryset.values_list("id", "request_payload")
    for task_id, payload in task_payloads:
        if refs_by_task.get(int(task_id)):
            continue
        payload = payload if isinstance(payload, dict) else {}
        config_id = payload.get("backup_config_id")
        source_type = str(payload.get("source_type") or "")
        source_ref_id = payload.get("source_ref_id")
        fallback_ref = None
        try:
            if config_id:
                fallback_ref = ("backup_config", int(config_id))
            elif source_ref_id and source_type in {"agent", "nas"}:
                fallback_ref = (
                    "node" if source_type == "agent" else "source_resource",
                    int(source_ref_id),
                )
        except (TypeError, ValueError):
            fallback_ref = None
        if fallback_ref is not None:
            refs.append(fallback_ref)
            refs_by_task.setdefault(int(task_id), []).append(fallback_ref)
    allowed = visible(request, refs)
    if allowed is None:
        return queryset
    allowed_task_ids = {
        task_id
        for task_id, task_refs in refs_by_task.items()
        if any(ref in allowed for ref in task_refs)
    }
    # Organization-scoped roles may see operational/system tasks that do not
    # carry a stable Host resource reference (for example migration or
    # maintenance tasks). Resource-scoped roles remain fail-closed for those
    # rows because there is no owner reference to authorize.
    org_key = resolve_org_key(request)
    can_see_organization = bool(
        org_key
        and getattr(provider, "resource_scope_for_role", None)
        and provider.resource_scope_for_role(request.user, org_key) == "organization"
    )
    if can_see_organization:
        unscoped_task_ids = set(task_ids) - set(refs_by_task)
        allowed_task_ids.update(unscoped_task_ids)
    return queryset.filter(id__in=allowed_task_ids)
