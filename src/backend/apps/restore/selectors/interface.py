from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q, QuerySet

from apps.protection.models import BackupSourceSnapshot
from apps.restore.models import RestorePlan, RestoreRecord
from apps.task.models import Task


def restore_plans_queryset(*, organization_id: int) -> QuerySet[RestorePlan]:
    return RestorePlan.objects.filter(organization_id=organization_id)


def filter_restore_plans(
    queryset: QuerySet[RestorePlan],
    *,
    backup_config_id: int | None = None,
    source_type: str | None = None,
    source_ref_id: int | None = None,
    enabled: bool | None = None,
) -> QuerySet[RestorePlan]:
    if backup_config_id is not None:
        queryset = queryset.filter(backup_config_id=backup_config_id)
    if source_type:
        queryset = queryset.filter(source_type=source_type)
    if source_ref_id is not None:
        queryset = queryset.filter(source_ref_id=source_ref_id)
    if enabled is not None:
        queryset = queryset.filter(enabled=enabled)
    return queryset.order_by("source_type", "source_ref_id", "sort_order", "id")


def get_restore_plan(*, organization_id: int, plan_id: int) -> RestorePlan | None:
    return RestorePlan.objects.filter(organization_id=organization_id, pk=plan_id).first()


def restore_records_queryset(*, organization_id: int) -> QuerySet[RestoreRecord]:
    return (
        RestoreRecord.objects.filter(
            organization_id=organization_id,
            purpose=RestoreRecord.Purpose.USER_DATA,
        )
        .prefetch_related("items")
        .order_by("-created_at", "-id")
    )


def filter_restore_records(
    queryset: QuerySet[RestoreRecord],
    *,
    organization_id: int,
    source_type: str | None = None,
    source_ref_id: int | None = None,
    task_uuid: str | None = None,
    search: str | None = None,
    search_fields: Iterable[str] | None = None,
    status: str | None = None,
    source_mode: str | None = None,
    created_from=None,
    created_to=None,
) -> QuerySet[RestoreRecord]:
    if source_type:
        queryset = queryset.filter(source_type=source_type)
    if source_ref_id is not None:
        queryset = queryset.filter(source_ref_id=source_ref_id)
    if task_uuid:
        queryset = queryset.filter(task_uuid=task_uuid)
    if status:
        task_uuids = Task.objects.filter(
            organization_id=organization_id,
            status=status,
        ).values("task_uuid")
        queryset = queryset.filter(task_uuid__in=task_uuids)
    if source_mode:
        queryset = queryset.filter(source_mode=source_mode)
    if created_from is not None:
        queryset = queryset.filter(created_at__gte=created_from)
    if created_to is not None:
        queryset = queryset.filter(created_at__lte=created_to)
    query = (search or "").strip()
    if query:
        fields = set(search_fields or ["restore_uid"])
        predicate = Q()
        if "restore_uid" in fields:
            predicate |= Q(restore_uid__icontains=query)
        if "snapshot_uid" in fields:
            snapshot_ids = BackupSourceSnapshot.objects.filter(
                organization_id=organization_id,
                snapshot_uid__icontains=query,
            ).values("id")
            predicate |= Q(source_snapshot_id__in=snapshot_ids)
        if "task_uuid" in fields:
            matching_tasks = Task.objects.filter(
                organization_id=organization_id,
                task_uuid__icontains=query,
            ).values("task_uuid")
            predicate |= Q(task_uuid__in=matching_tasks)
        queryset = queryset.filter(predicate)
    return queryset


def get_restore_record(*, organization_id: int, record_id: int) -> RestoreRecord | None:
    return restore_records_queryset(organization_id=organization_id).filter(pk=record_id).first()


__all__ = [
    "filter_restore_plans",
    "filter_restore_records",
    "get_restore_plan",
    "get_restore_record",
    "restore_plans_queryset",
    "restore_records_queryset",
]
