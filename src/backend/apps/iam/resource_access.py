"""Resource visibility helpers shared by Host business endpoints."""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from rest_framework.exceptions import PermissionDenied

from apps.iam.permissions_org import resolve_org_key
from common.extension_spi import get_authz_provider


def resolve_resource_reference(
    *, organization_id: int, resource_type: str, source_id: Any
) -> int:
    """Use the optional governance projection for non-integer Host resource IDs."""
    if isinstance(source_id, int) or (
        resource_type != "alert_policy" and str(source_id).isdigit()
    ):
        return int(source_id)
    provider = get_authz_provider()
    resolver = getattr(provider, "resolve_resource_reference", None)
    if resolver is None:
        raise PermissionDenied("This resource identifier is not supported.")
    try:
        return int(
            resolver(
                organization_id=organization_id,
                resource_type=resource_type,
                source_id=source_id,
            )
        )
    except (ValueError, TypeError):
        raise PermissionDenied("Invalid resource identifier.") from None


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
    normalized = tuple(
        (str(resource_type), int(resource_id)) for resource_type, resource_id in refs
    )
    if not normalized:
        return set()
    if getattr(request, "hfl_support_readonly", False):
        if request.method in ("GET", "HEAD", "OPTIONS") and (
            action
            in ("resources.view", "resource.view", "protection.view", "operations.view")
            or action.endswith(".view")
        ):
            return set(normalized)
        return set()
    from apps.iam.query_authorization import request_authorization

    context = request_authorization(
        request, fresh=request.method not in ("GET", "HEAD", "OPTIONS"),
    )
    if context is not None:
        return context.visible_refs(normalized, action=action)
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
    if get_authz_provider() is None:
        return queryset
    from apps.iam.query_authorization import request_authorization

    context = request_authorization(
        request, fresh=request.method not in ("GET", "HEAD", "OPTIONS"),
    )
    if context is not None and not getattr(request, "hfl_support_readonly", False):
        return context.filter_queryset(queryset, resource_type, action=action)
    from apps.iam.org_context import require_org

    org = require_org(request)
    resource_ids = list(queryset.values_list("pk", flat=True))
    if not resource_ids:
        return queryset
    references = {
        source_id: resolve_resource_reference(
            organization_id=org.id,
            resource_type=resource_type,
            source_id=source_id,
        )
        for source_id in resource_ids
    }
    visible_refs = visible_resource_refs(
        request,
        [(resource_type, references[source_id]) for source_id in resource_ids],
        action=action,
    )
    if visible_refs is None:
        return queryset
    visible_ids = [
        resource_id
        for resource_type_key, resource_id in visible_refs
        if resource_type_key == resource_type
    ]
    allowed = set(visible_ids)
    return queryset.filter(
        pk__in=[
            source_id for source_id in resource_ids if references[source_id] in allowed
        ]
    )


def filter_queryset_by_resource_field(
    request: Any,
    queryset: QuerySet,
    resource_type: str,
    resource_id_field: str,
    *,
    action: str = "resources.view",
) -> QuerySet:
    """Filter derived rows through the owner of a referenced Host resource."""
    from apps.iam.query_authorization import request_authorization

    context = request_authorization(request)
    if context is not None and not getattr(request, "hfl_support_readonly", False):
        model = context.resource_model(resource_type)
        if model is None:
            return queryset.none()
        allowed = context.filter_queryset(model.objects.all(), resource_type, action=action)
        return queryset.filter(**{f"{resource_id_field}__in": allowed.values("pk")})
    resource_ids = list(queryset.values_list(resource_id_field, flat=True).distinct())
    visible = visible_resource_refs(
        request,
        [
            (resource_type, int(resource_id))
            for resource_id in resource_ids
            if resource_id
        ],
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
    resource_id: Any,
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
        resource_id=resolve_resource_reference(
            organization_id=organization_id,
            resource_type=resource_type,
            source_id=resource_id,
        ),
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
    from apps.iam.query_authorization import request_authorization

    context = request_authorization(request)
    if context is not None and not getattr(request, "hfl_support_readonly", False):
        return _filter_task_sql(queryset, context)
    if getattr(request, "hfl_support_readonly", False) and request.method in {
        "GET", "HEAD", "OPTIONS",
    }:
        # Platform support sessions are explicitly read-only and tenant-bound
        # by get_membership(); keep their historical task view consistent
        # after the source's live identity has been removed.
        from apps.iam.models import Organization

        org = Organization.objects.filter(key=resolve_org_key(request)).first()
        return queryset.filter(organization_id=org.pk) if org is not None else queryset.none()

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
            mapped_type = (
                "repository" if resource_type != "backup_config" else resource_type
            )
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
    allowed = visible(request, refs, action="operations.tasks.view")
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
        and provider.has_org_action(request.user, org_key, "operations.tasks.view")
        and getattr(provider, "resource_scope_for_role", None)
        and provider.resource_scope_for_role(request.user, org_key) == "organization"
    )
    if can_see_organization:
        unscoped_task_ids = set(task_ids) - set(refs_by_task)
        allowed_task_ids.update(unscoped_task_ids)
    return queryset.filter(id__in=allowed_task_ids)


def _filter_task_sql(queryset: QuerySet, context: Any) -> QuerySet:
    """Task reads use current grants, including retained resource identities."""
    from django.db.models import BigIntegerField, Case, F, Q, Value, When
    from django.db.models.fields.json import KeyTextTransform
    from django.db.models.functions import Cast
    from apps.task.models import TaskResource

    action = "operations.tasks.view"
    if not context.has_action("backup_config", action):
        return queryset.none()
    fence = getattr(context, "fence_queryset", None)
    if not callable(fence):
        return queryset.none()
    queryset = fence(queryset)
    queryset = queryset.filter(organization_id=context.org.pk)
    if context.has_org_action(action) and context.has_org_action("configuration.resources.access_all"):
        # History belongs to the organization, even after its live target is
        # removed. The actor/Role fence above still observes revocation.
        return queryset
    history_scope = getattr(context, "filter_task_resource_queryset", None)
    if not callable(history_scope):
        history_scope = context.filter_queryset
    mapping = (
        ("node", Q(resource_type="host") | Q(resource_type="backup_source", resource_subtype="agent")),
        ("source_resource", Q(resource_type="backup_source", resource_subtype="nas")),
        ("backup_config", Q(resource_type="backup_config")),
        ("repository", Q(resource_type__in=("repository", "target_repository"))),
    )
    references = TaskResource.objects.filter(task__organization_id=context.org.pk)
    recognized = Q(pk__in=[])
    allowed_bindings = Q(pk__in=[])
    for kind, types in mapping:
        recognized |= types
        model = context.resource_model(kind)
        manager = getattr(model, "all_objects", model.objects)
        allowed = history_scope(manager.all(), kind, action=action)
        # Build each authorized resource set independently of the TaskResource
        # row. Correlating every nested grant to every task causes huge plans
        # and expensive PostgreSQL JIT compilation for Group-only readers.
        allowed_bindings |= types & Q(resource_id__in=allowed.order_by().values("pk"))
    queryset = queryset.alias(
        _hfl_config_text=KeyTextTransform("backup_config_id", "request_payload"),
        _hfl_source_text=KeyTextTransform("source_ref_id", "request_payload"),
    )
    for name in ("config", "source"):
        field = f"_hfl_{name}_text"
        numeric = Q(**{f"{field}__regex": r"^[0-9]{1,18}$"}) | (
            Q(**{f"{field}__regex": r"^[0-9]{19}$"})
            & Q(**{f"{field}__lte": "9223372036854775807"})
        )
        queryset = queryset.alias(**{f"_hfl_{name}_ref": Case(
            When(numeric, then=Cast(F(field), BigIntegerField())),
            default=Value(None), output_field=BigIntegerField(),
        )})
    condition = Q(pk__in=references.filter(allowed_bindings).order_by().values("task_id"))
    config = history_scope(
        context.resource_model("backup_config").objects.all(), "backup_config", action=action,
    )
    fallback = Q(_hfl_config_ref__in=config.order_by().values("pk"))
    for kind, source_type in (("node", "agent"), ("source_resource", "nas")):
        model = context.resource_model(kind)
        manager = getattr(model, "all_objects", model.objects)
        allowed = history_scope(manager.all(), kind, action=action)
        fallback |= Q(
            _hfl_config_ref__isnull=True,
            request_payload__source_type=source_type,
            _hfl_source_ref__in=allowed.order_by().values("pk"),
        )
    condition |= ~Q(pk__in=references.filter(recognized).order_by().values("task_id")) & fallback
    return queryset.filter(condition)
