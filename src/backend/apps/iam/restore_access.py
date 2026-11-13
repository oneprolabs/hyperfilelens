"""Shared business and personal scope for restore records and their tasks."""

from typing import Any

from django.db.models import Q, QuerySet


def filter_restore_record_scope(
    queryset: QuerySet, context: Any, *, action: str, task_history: bool = False,
) -> QuerySet:
    """Authorize the plan first, then configuration, then legacy source.

    A linked plan is authoritative even when the caller owns the execution
    source. Personal Chat restores additionally require the caller's session.
    Task history may use retained source identities after deregistration,
    without making deleted sources reappear in ordinary business directories.
    """
    if not context.valid:
        return queryset.none()
    from apps.lens_bridge.models import LensSessionLink, LensWorkspaceBinding
    from apps.restore.models import RestoreRecord

    def ids(kind: str) -> QuerySet:
        model = context.resource_model(kind)
        manager = getattr(model, "all_objects", model.objects) if task_history else model.objects
        scope = context.filter_task_resource_queryset if task_history else context.filter_queryset
        return scope(
            manager.all(), kind, action=action,
        ).order_by().values("pk")

    visible = Q(plan_id__in=ids("restore_plan")) | (
        Q(plan_id__isnull=True) & Q(backup_config_id__in=ids("backup_config"))
    )
    visible |= Q(plan_id__isnull=True, backup_config_id__isnull=True) & (
        Q(source_type="agent", source_ref_id__in=ids("node"))
        | Q(source_type="nas", source_ref_id__in=ids("source_resource"))
    )
    personal_sources = LensSessionLink.objects.filter(
        organization_id=context.org.pk, hfl_user_id=context.user.pk,
    ).values("knowledge_source_id")
    workspaces = LensWorkspaceBinding.objects.filter(
        organization_id=context.org.pk, knowledge_source_id__in=personal_sources,
    ).values("pk")
    personal = ~Q(purpose=RestoreRecord.Purpose.LENS_WORKSPACE) | Q(
        workspace_binding_id__in=workspaces,
    )
    return context.fence_queryset(queryset.filter(
        organization_id=context.org.pk,
    ).filter(visible & personal))


def restore_record_detail_projection(request: Any, records: Any) -> set[int] | None:
    """Batch raw-detail rights independently from visible restore status.

    Check persisted endpoints and item repositories even when legacy tasks
    have incomplete resource bindings. The Restore permission is sufficient;
    this must not introduce an implicit requirement for Tasks permission.
    """
    from django.db.models import Exists, OuterRef

    from apps.iam.query_authorization import request_authorization
    from apps.iam.task_projection import filter_task_detail_queryset
    from apps.restore.models import RestoreRecord, RestoreRecordItem
    from apps.task.models import Task

    context = request_authorization(request)
    if context is None or getattr(request, "hfl_support_readonly", False):
        return None
    if not context.valid:
        return set()
    action = "protection.restore_plans.view"
    if context.has_org_action(action) and context.has_organization_resource_scope():
        return None
    scopes = {}
    for kind in ("node", "source_resource", "backup_config", "repository"):
        model = context.resource_model(kind)
        scopes[kind] = context.filter_queryset(
            model.objects.all(), kind, action=action,
        ).order_by().values("pk")
    rows = filter_restore_record_scope(
        RestoreRecord.objects.filter(pk__in=[record.pk for record in records]),
        context, action=action,
    )
    for endpoint in ("source", "target"):
        rows = rows.filter(
            Q(**{f"{endpoint}_type": "agent", f"{endpoint}_ref_id__in": scopes["node"]})
            | Q(**{f"{endpoint}_type": "nas", f"{endpoint}_ref_id__in": scopes["source_resource"]})
        )
    rows = rows.filter(
        Q(backup_config_id__isnull=True) | Q(backup_config_id__in=scopes["backup_config"]),
    )
    unsafe_items = RestoreRecordItem.objects.filter(restore_record_id=OuterRef("pk")).exclude(
        organization_id=context.org.pk, repository_id__in=scopes["repository"],
    )
    tasks = Task.objects.filter(pk=OuterRef("task_id"))
    raw_tasks = filter_task_detail_queryset(
        tasks.filter(task_uuid=OuterRef("task_uuid")), context, action=action,
    )
    rows = rows.filter(
        ~Q(Exists(unsafe_items)),
        ~Q(Exists(tasks)) | Q(Exists(raw_tasks)),
    )
    return set(rows.values_list("pk", flat=True))
