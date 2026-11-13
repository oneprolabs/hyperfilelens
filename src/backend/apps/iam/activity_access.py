"""Visibility of derived activity follows its underlying resource, not the page."""

from apps.iam.permissions_org import resolve_org_key
from common.extension_spi import get_authz_provider


ALIASES = {
    "agent": "node",
    "proxy": "node",
    "gateway": "node",
    "host": "node",
    "agent_proxy": "node",
    "sync_proxy": "node",
    "backup_repository": "repository",
    "target_storage": "repository",
    "policy": "backup_policy",
    "restore_restore_plan": "restore_plan",
}


def filter_activity_queryset(request, queryset, *, actor_field=None, action=None):
    provider = get_authz_provider()
    if provider is None:
        return queryset
    if getattr(request, "hfl_support_readonly", False):
        return (
            queryset
            if request.method in ("GET", "HEAD", "OPTIONS")
            else queryset.none()
        )
    org_key = resolve_org_key(request)
    action = action or (
        "operations.audit_logs.view"
        if "audit" in request.path
        else "operations.alerts.view" if "alert" in request.path else "operations.events.view"
    )
    from apps.iam.query_authorization import request_authorization

    context = request_authorization(request)
    if context is not None:
        return _filter_activity_sql(request, queryset, context, actor_field, action)
    if provider.get_org_role(request.user, org_key) is None:
        return queryset.none()
    if provider.has_org_action(
        request.user, org_key, action
    ) and provider.resource_scope_for_role(request.user, org_key) == "organization":
        return queryset
    fields = ["pk", "resource_type", "resource_id"]
    if actor_field:
        fields.append(actor_field)
    rows = list(queryset.values(*fields))
    refs_by_pk = {}
    own_records = set()
    from apps.iam.org_context import require_org

    org = require_org(request)
    for row in rows:
        if (
            actor_field and row[actor_field] == request.user.id
            and row["resource_type"] == "user"
            and str(row["resource_id"]) == str(request.user.pk)
            and provider.has_org_action(request.user, org_key, action)
        ):
            own_records.add(row["pk"])
        kind = ALIASES.get(row["resource_type"], row["resource_type"])
        raw = str(row["resource_id"] or "")
        if raw.isdigit():
            refs_by_pk[row["pk"]] = (kind, int(raw))
        elif kind == "alert_policy" and raw:
            resolver = getattr(provider, "resolve_resource_reference", None)
            if resolver:
                try:
                    refs_by_pk[row["pk"]] = (
                        kind,
                        resolver(
                            organization_id=org.id,
                            resource_type=kind,
                            source_id=raw,
                        ),
                    )
                except (TypeError, ValueError):
                    pass
    refs = list(refs_by_pk.values())
    allowed = provider.visible_resource_refs(request.user, org_key, refs, action=action)
    visible_pks = own_records | {pk for pk, ref in refs_by_pk.items() if ref in allowed}
    return queryset.filter(pk__in=visible_pks)


def _filter_activity_sql(request, queryset, context, actor_field, action):
    """Derived records authorize their referenced live resource before paging."""
    from django.db.models import BigIntegerField, Case, CharField, Exists, F, OuterRef, Q, Value, When
    from django.db.models.functions import Cast

    if not context.has_action("backup_config", action):
        return queryset.none()
    fence = getattr(context, "fence_queryset", None)
    if not callable(fence):
        return queryset.none()
    queryset = fence(queryset)
    queryset = queryset.filter(organization_id=context.org.pk)
    numeric = Q(resource_id__regex=r"^[0-9]{1,18}$") | (
        Q(resource_id__regex=r"^[0-9]{19}$") & Q(resource_id__lte="9223372036854775807")
    )
    queryset = queryset.annotate(_hfl_record_ref=Case(
        When(numeric, then=Cast(F("resource_id"), BigIntegerField())),
        default=Value(None), output_field=BigIntegerField(),
    ))
    task_condition = _task_activity_condition(context, action)
    if context.has_org_action(action) and context.has_organization_resource_scope():
        # Organization scope does not include someone else's personal Chat
        # restore tasks, including errors copied into their activity records.
        return queryset.filter(~Q(resource_type="task") | task_condition)
    condition = Q(pk__in=[])
    if actor_field and context.has_org_action(action):
        condition |= Q(**{actor_field: request.user.pk}, resource_type="user", resource_id=str(request.user.pk))
    kinds = ("node", "repository", "source_resource", "backup_config", "restore_plan",
             "backup_policy", "file_filter_rule", "alert_policy", "notification_channel", "source_tag")
    for kind in kinds:
        names = [kind, *(alias for alias, actual in ALIASES.items() if actual == kind)]
        model = context.resource_model(kind)
        allowed = context.filter_queryset(model.objects.all(), kind, action=action)
        if kind == "alert_policy":
            allowed = allowed.annotate(_uuid_text=Cast(F("pk"), CharField())).filter(
                Q(_hfl_ref=OuterRef("_hfl_record_ref"))
                | Q(_uuid_text=OuterRef("resource_id"))
            )
        else:
            allowed = allowed.filter(pk=OuterRef("_hfl_record_ref"))
        condition |= Q(resource_type__in=names) & Q(Exists(allowed))
    deleted_scope = getattr(get_authz_provider(), "filter_deleted_alert_records", None)
    if callable(deleted_scope):
        deleted = deleted_scope(queryset, context, action=action)
        condition |= Q(pk__in=deleted.order_by().values("pk"))
    return queryset.filter(condition | task_condition)


def _task_activity_condition(context, action):
    """Authorize raw Task-derived content, including organization-wide reads."""
    from django.db.models import CharField, Exists, OuterRef, Q
    from django.db.models.functions import Cast

    from apps.iam.resource_access import _filter_task_sql
    from apps.iam.task_projection import filter_task_detail_queryset
    from apps.task.models import Task

    # Activity contains raw messages/metadata, not the safe task status
    # projection. Exclude unsafe rows before search, statistics and pagination.
    tasks = filter_task_detail_queryset(
        _filter_task_sql(Task.objects.all(), context, action=action),
        context, action=action,
    ).annotate(
        _hfl_task_uuid=Cast("task_uuid", CharField()),
    )
    return Q(resource_type="task") & Q(Exists(tasks.filter(
        Q(pk=OuterRef("_hfl_record_ref")) | Q(_hfl_task_uuid=OuterRef("resource_id")),
    )))
