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
    role = provider.get_org_role(request.user, org_key)
    if role is None:
        return queryset.none()
    action = action or (
        "operations.audit_logs.view"
        if "audit" in request.path
        else "operations.alerts.view" if "alert" in request.path else "operations.events.view"
    )
    from apps.iam.query_authorization import request_authorization

    context = request_authorization(request)
    if context is not None:
        return _filter_activity_sql(request, queryset, context, actor_field, action)
    if provider.has_org_action(
        request.user, org_key, action
    ) and provider.has_org_action(
        request.user,
        org_key,
        "configuration.resources.access_all",
    ):
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
    if context.has_org_action(action) and context.has_org_action(
        "configuration.resources.access_all",
    ):
        return queryset
    numeric = Q(resource_id__regex=r"^[0-9]{1,18}$") | (
        Q(resource_id__regex=r"^[0-9]{19}$") & Q(resource_id__lte="9223372036854775807")
    )
    queryset = queryset.annotate(_hfl_record_ref=Case(
        When(numeric, then=Cast(F("resource_id"), BigIntegerField())),
        default=Value(None), output_field=BigIntegerField(),
    ))
    condition = Q(pk__in=[])
    if actor_field and context.has_org_action(action):
        condition |= Q(**{actor_field: request.user.pk})
    kinds = ("node", "repository", "source_resource", "backup_config", "restore_plan",
             "backup_policy", "file_filter_rule", "alert_policy", "notification_channel")
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
    return queryset.filter(condition)
