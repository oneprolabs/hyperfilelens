from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q, QuerySet

from apps.protection.models import (
    BackupConfig,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)

ALLOWED_ORDERING = {
    "created_at",
    "-created_at",
    "started_at",
    "-started_at",
    "finished_at",
    "-finished_at",
}


def _ordering(value: str | None) -> list[str]:
    if value in ALLOWED_ORDERING:
        return [value or "-created_at", "-id"]
    return ["-created_at", "-id"]


def backup_source_snapshots_queryset(
    *,
    organization_id: int,
    include_deleted: bool = False,
) -> QuerySet[BackupSourceSnapshot]:
    queryset = BackupSourceSnapshot.objects.filter(organization_id=organization_id)
    if not include_deleted:
        queryset = queryset.filter(deleted_at__isnull=True).exclude(
            status__in=[
                BackupSourceSnapshot.Status.DELETING,
                BackupSourceSnapshot.Status.DELETE_FAILED,
                BackupSourceSnapshot.Status.DELETED,
            ]
        )
    return queryset.prefetch_related("directories")


def filter_backup_source_snapshots(
    queryset: QuerySet[BackupSourceSnapshot],
    *,
    organization_id: int,
    source_type: str | None = None,
    source_ref_id: int | None = None,
    backup_config_id: int | None = None,
    repository_id: int | None = None,
    status: str | None = None,
    statuses: Iterable[str] | None = None,
    exclude_statuses: Iterable[str] | None = None,
    created_from=None,
    created_to=None,
    started_from=None,
    started_to=None,
    snapshot_uid: str | None = None,
    search: str | None = None,
    ordering: str | None = None,
) -> QuerySet[BackupSourceSnapshot]:
    if source_type:
        queryset = queryset.filter(source_type=source_type)
    if source_ref_id is not None:
        queryset = queryset.filter(source_ref_id=source_ref_id)
    if backup_config_id is not None:
        queryset = queryset.filter(backup_config_id=backup_config_id)
    if repository_id is not None:
        queryset = queryset.filter(repository_id=repository_id)
    included = [value for value in (statuses or []) if value]
    if included:
        queryset = queryset.filter(status__in=included)
    elif status:
        queryset = queryset.filter(status=status)
    excluded = [value for value in (exclude_statuses or []) if value]
    if excluded:
        queryset = queryset.exclude(status__in=excluded)
    if created_from is not None:
        queryset = queryset.filter(created_at__gte=created_from)
    if created_to is not None:
        queryset = queryset.filter(created_at__lte=created_to)
    if started_from is not None:
        queryset = queryset.filter(started_at__gte=started_from)
    if started_to is not None:
        queryset = queryset.filter(started_at__lte=started_to)
    snapshot_uid_query = str(snapshot_uid or "").strip()
    if snapshot_uid_query:
        queryset = queryset.filter(snapshot_uid__icontains=snapshot_uid_query)

    return _apply_search(
        queryset=queryset,
        organization_id=organization_id,
        search=search,
    ).order_by(*_ordering(ordering))


def _apply_search(
    *,
    queryset: QuerySet[BackupSourceSnapshot],
    organization_id: int,
    search: str | None,
) -> QuerySet[BackupSourceSnapshot]:
    query = str(search or "").strip()
    if not query:
        return queryset
    config_ids = list(
        BackupConfig.objects.filter(
            organization_id=organization_id,
            name__icontains=query,
        ).values_list("id", flat=True)[:500]
    )
    snapshot_ids = list(
        BackupSourceSnapshotDirectory.objects.filter(
            organization_id=organization_id,
            source_path__icontains=query,
        ).values_list("source_snapshot_id", flat=True)[:500]
    )
    return queryset.filter(
        Q(snapshot_uid__icontains=query)
        | Q(backup_config_id__in=config_ids)
        | Q(id__in=snapshot_ids)
    )


def get_backup_source_snapshot(
    *,
    organization_id: int,
    snapshot_id: int,
    include_deleted: bool = False,
) -> BackupSourceSnapshot | None:
    return (
        backup_source_snapshots_queryset(
            organization_id=organization_id,
            include_deleted=include_deleted,
        )
        .filter(pk=snapshot_id)
        .first()
    )
