"""Batch detail projection for tasks whose execution dependencies are private."""

from collections import defaultdict
from typing import Any

from django.db.models import (
    BigIntegerField, Case, Exists, F, OuterRef, Q, QuerySet, Value, When,
)
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast

from apps.iam.query_authorization import request_authorization


PAYLOAD_REFERENCES = (
    ("backup_config_id", "backup_config"),
    ("repository_id", "repository"),
    ("target_repository_id", "repository"),
    ("restore_plan_id", "restore_plan"),
)


def numeric_payload_reference(queryset: QuerySet, field: str) -> tuple[QuerySet, str]:
    """Alias a bounded numeric JSON reference without unsafe bigint casts."""
    text = f"_hfl_detail_{field}_text"
    ref = f"_hfl_detail_{field}_ref"
    queryset = queryset.alias(**{text: KeyTextTransform(field, "request_payload")})
    numeric = Q(**{f"{text}__regex": r"^[0-9]{1,18}$"}) | (
        Q(**{f"{text}__regex": r"^[0-9]{19}$"})
        & Q(**{f"{text}__lte": "9223372036854775807"})
    )
    return queryset.alias(**{
        ref: Case(
            When(numeric, then=Cast(F(text), BigIntegerField())),
            default=Value(None), output_field=BigIntegerField(),
        ),
    }), ref


def _filter_snapshot_download_details(
    queryset: QuerySet, context: Any, scopes: dict[str, QuerySet],
) -> QuerySet:
    """Require the download's actual directory and configuration lineage.

    Download tasks bind only their source in TaskResource. Their payload
    references the snapshot and directory instead of the configuration, also
    for batch downloads. Group downloads normalize all directories against
    this same snapshot before creating the task.
    """
    from apps.protection.models import BackupSourceSnapshotDirectory
    from apps.task.models import Task

    queryset, snapshot_ref = numeric_payload_reference(queryset, "source_snapshot_id")
    queryset, directory_ref = numeric_payload_reference(
        queryset, "source_snapshot_directory_id",
    )
    queryset, repository_ref = numeric_payload_reference(queryset, "repository_id")
    directories = BackupSourceSnapshotDirectory.objects.filter(
        pk=OuterRef(directory_ref),
        organization_id=context.org.pk,
        source_snapshot_id=OuterRef(snapshot_ref),
        source_snapshot__organization_id=context.org.pk,
        source_snapshot__backup_config_id=F("backup_config_id"),
        source_snapshot__repository_id=F("repository_id"),
        backup_config_id__in=scopes["backup_config"],
        repository_id__in=scopes["repository"],
        repository_id=OuterRef(repository_ref),
    )
    # Missing, malformed, foreign or dangling references must not grant raw
    # access. Keep the authorized basic task status; only redact its details.
    return queryset.filter(
        ~Q(task_type=Task.Type.SNAPSHOT_DOWNLOAD) | Q(Exists(directories)),
    )


def filter_task_detail_queryset(
    queryset: QuerySet, context: Any, *, action: str,
) -> QuerySet:
    """Keep raw-safe tasks in SQL, before derived activity search/count/page.

    Task status visibility is checked by the caller. Raw errors and logs also
    require every execution dependency; an alert must not re-expose details
    that the task serializer itself hides. The action belongs to the caller's
    domain (Tasks, Alerts or Notification Records), not necessarily Tasks.
    """
    if not context.valid:
        return queryset.none()
    queryset = context.fence_queryset(
        queryset.filter(organization_id=context.org.pk),
    )
    if context.has_org_action(action) and context.has_organization_resource_scope():
        return queryset
    from apps.protection.models import BackupSourceSnapshot
    from apps.task.models import TaskResource

    scopes = {}
    for kind in ("node", "source_resource", "backup_config", "repository", "restore_plan"):
        model = context.resource_model(kind)
        manager = getattr(model, "all_objects", model.objects)
        scopes[kind] = context.filter_task_resource_queryset(
            manager.all(), kind, action=action,
        ).order_by().values("pk")
    mapping = (
        ("node", Q(resource_type__in=("node", "host")) | Q(
            resource_type="backup_source", resource_subtype="agent",
        )),
        ("source_resource", Q(resource_type="source_resource") | Q(
            resource_type="backup_source", resource_subtype="nas",
        )),
        ("backup_config", Q(resource_type="backup_config")),
        ("repository", Q(resource_type__in=("repository", "target_repository"))),
        ("restore_plan", Q(resource_type="restore_plan")),
    )
    allowed = Q(pk__in=[])
    for kind, types in mapping:
        allowed |= types & Q(resource_id__in=scopes[kind])
    snapshots = BackupSourceSnapshot.objects.filter(
        organization_id=context.org.pk,
        backup_config_id__in=scopes["backup_config"],
    ).order_by().values("pk")
    allowed |= Q(resource_type="snapshot", resource_id__in=snapshots)
    denied = TaskResource.objects.filter(task_id=OuterRef("pk")).exclude(allowed)
    queryset = queryset.filter(~Q(Exists(denied)))
    for field, kind in PAYLOAD_REFERENCES:
        queryset, ref = numeric_payload_reference(queryset, field)
        # Missing/null references are optional. Malformed, deleted or foreign
        # references must not accidentally authorize raw payloads.
        queryset = queryset.filter(
            Q(**{f"request_payload__{field}__isnull": True})
            | Q(**{f"request_payload__{field}": None})
            | Q(**{f"{ref}__in": scopes[kind]}),
        )
    return _filter_snapshot_download_details(queryset, context, scopes)


def _reference(row):
    kind = row.resource_type
    if kind == "backup_source":
        kind = {"agent": "node", "nas": "source_resource"}.get(row.resource_subtype)
    else:
        kind = {"host": "node", "target_repository": "repository"}.get(kind, kind)
    return kind, row.resource_id


def task_detail_projection(request, tasks):
    """Return safe resource refs and whether raw details can be disclosed.

    The caller already authorized the task's primary business resource. A
    shared executor/repository must not make another resource's raw payload,
    logs or dependency metadata visible. This does not change execution rights.
    """
    context = request_authorization(request)
    if context is None or not context.valid or getattr(request, "hfl_support_readonly", False):
        return None
    if (
        context.has_org_action("operations.tasks.view")
        and context.has_organization_resource_scope()
    ):
        return None
    by_task = {}
    by_kind = defaultdict(set)
    for task in tasks:
        refs = {_reference(row) for row in task.resources.all()}
        payload = task.request_payload if isinstance(task.request_payload, dict) else {}
        for field, kind in PAYLOAD_REFERENCES:
            value = payload.get(field)
            if type(value) is int and 0 < value <= 2**63 - 1:
                refs.add((kind, value))
        by_task[task.pk] = refs
        for kind, pk in refs:
            by_kind[kind].add(pk)
    allowed = set()
    for kind, ids in by_kind.items():
        model = context.resource_model(kind)
        if model is None:
            continue
        manager = getattr(model, "all_objects", model.objects)
        rows = context.filter_task_resource_queryset(
            manager.filter(pk__in=ids),
            kind,
            action="operations.tasks.view",
        )
        allowed.update((kind, pk) for pk in rows.values_list("pk", flat=True))
    if "snapshot" in by_kind:
        from apps.protection.models import BackupSourceSnapshot

        configs = context.filter_queryset(
            context.resource_model("backup_config").objects.all(),
            "backup_config",
            action="operations.tasks.view",
        )
        snapshots = BackupSourceSnapshot.objects.filter(
            organization_id=context.org.pk,
            pk__in=by_kind["snapshot"],
            backup_config_id__in=configs.values("pk"),
        ).values_list("pk", flat=True)
        allowed.update(("snapshot", pk) for pk in snapshots)
    from apps.task.models import Task

    raw_ids = set(filter_task_detail_queryset(
        Task.objects.filter(pk__in=by_task), context, action="operations.tasks.view",
    ).values_list("pk", flat=True))
    return {
        pk: {"refs": refs & allowed, "details": pk in raw_ids}
        for pk, refs in by_task.items()
    }
