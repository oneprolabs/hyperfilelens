from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.protection.models import (
    BackupConfig,
    BackupConfigDirectory,
    BackupSourceSnapshot,
    BackupSourceSnapshotDirectory,
)
from apps.protection.services.backup_runtime_policy import build_backup_runtime_policy
from apps.protection.services.kopia_snapshot_delete import normalize_kopia_snapshot_id
from apps.storage.repositories.models import Repository

_TERMINAL_DIRECTORY_STATUSES = {
    BackupSourceSnapshotDirectory.Status.AVAILABLE,
    BackupSourceSnapshotDirectory.Status.FAILED,
    BackupSourceSnapshotDirectory.Status.CANCELLED,
    BackupSourceSnapshotDirectory.Status.DELETED,
}


@dataclass(frozen=True)
class SnapshotDirectoryConfig:
    id: int
    path: str
    path_type: str
    display_name: str


def _snapshot_uid() -> str:
    return f"bss-{uuid.uuid4().hex[:20]}"


def _validated_snapshot_status(value: str | None) -> str:
    status = str(value or BackupSourceSnapshot.Status.CREATING).strip().lower()
    allowed = {choice for choice, _label in BackupSourceSnapshot.Status.choices}
    if status not in allowed:
        raise ValidationError({"status": "Unsupported backup source snapshot status."})
    return status


def _validated_directory_status(value: str | None) -> str:
    status = str(value or BackupSourceSnapshotDirectory.Status.CREATING).strip().lower()
    allowed = {choice for choice, _label in BackupSourceSnapshotDirectory.Status.choices}
    if status not in allowed:
        raise ValidationError({"status": "Unsupported backup source snapshot directory status."})
    return status


def _validated_trigger_type(value: str | None) -> str:
    trigger_type = str(value or BackupSourceSnapshot.TriggerType.MANUAL).strip().lower()
    allowed = {choice for choice, _label in BackupSourceSnapshot.TriggerType.choices}
    if trigger_type not in allowed:
        raise ValidationError({"trigger_type": "Unsupported trigger type."})
    return trigger_type


def _optional_nonnegative_stat(stats: dict[str, Any] | None, key: str) -> int | None:
    if not isinstance(stats, dict) or key not in stats:
        return None
    value = stats.get(key)
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


@transaction.atomic
def create_source_snapshot(
    *,
    organization_id: int,
    source_type: str,
    source_ref_id: int,
    backup_config_id: int,
    repository_id: int,
    task_id: int,
    task_uuid,
    idempotency_key: str,
    trigger_type: str = BackupSourceSnapshot.TriggerType.MANUAL,
    snapshot_uid: str | None = None,
    status: str = BackupSourceSnapshot.Status.CREATING,
    started_at=None,
    finished_at=None,
    directory_count: int = 0,
    metadata: dict[str, Any] | None = None,
    policy_snapshot: dict[str, Any] | None = None,
) -> BackupSourceSnapshot:
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=organization_id,
        idempotency_key=str(idempotency_key).strip(),
    ).first()
    if snapshot is not None:
        return snapshot

    config = BackupConfig.objects.filter(
        organization_id=organization_id,
        id=backup_config_id,
    ).first()
    if config is None:
        raise ValidationError({"backup_config_id": "Backup config not found."})
    if policy_snapshot is None:
        policy_snapshot = build_backup_runtime_policy(config=config)

    repository = Repository.objects.filter(
        organization_id=organization_id,
        id=repository_id,
    ).first()
    if repository is None:
        raise ValidationError({"repository_id": "Repository not found."})
    config_directories = list(config.directories.order_by("sort_order", "id"))
    snapshot_metadata = dict(metadata or {})
    snapshot_metadata["config_directories"] = [
        {
            "id": directory.id,
            "path": directory.path,
            "path_type": directory.path_type,
            "display_name": directory.display_name,
        }
        for directory in config_directories
    ]
    return BackupSourceSnapshot.objects.create(
        organization_id=organization_id,
        snapshot_uid=str(snapshot_uid or _snapshot_uid()).strip(),
        idempotency_key=str(idempotency_key).strip(),
        source_type=str(source_type).strip(),
        source_ref_id=int(source_ref_id),
        backup_config_id=int(backup_config_id),
        repository_id=int(repository_id),
        task_id=int(task_id),
        task_uuid=task_uuid,
        trigger_type=_validated_trigger_type(trigger_type),
        status=_validated_snapshot_status(status),
        started_at=started_at,
        finished_at=finished_at,
        directory_count=max(0, int(directory_count)),
        metadata=snapshot_metadata,
        policy_snapshot=policy_snapshot,
    )


@transaction.atomic
def set_source_snapshot_started(
    *,
    source_snapshot: BackupSourceSnapshot,
    started_at=None,
) -> BackupSourceSnapshot:
    if source_snapshot.started_at:
        return source_snapshot
    source_snapshot.started_at = started_at or timezone.now()
    source_snapshot.save(update_fields=["started_at", "updated_at"])
    return source_snapshot


@transaction.atomic
def record_source_snapshot_directory_result(
    *,
    source_snapshot: BackupSourceSnapshot,
    backup_config_dir_id: int,
    source_path: str,
    repository_id: int,
    status: str,
    path_type: str = BackupSourceSnapshotDirectory.PathType.UNKNOWN,
    display_name: str = "",
    kopia_snapshot_id: str | None = None,
    size_bytes: int = 0,
    file_count: int = 0,
    dir_count: int = 0,
    stats: dict[str, Any] | None = None,
    error_code: str = "",
    error_message: str = "",
) -> BackupSourceSnapshotDirectory:
    status_value = _validated_directory_status(status)
    path_type_value = str(path_type or BackupSourceSnapshotDirectory.PathType.UNKNOWN).strip().lower()
    allowed_path_types = {choice for choice, _label in BackupSourceSnapshotDirectory.PathType.choices}
    if path_type_value not in allowed_path_types:
        path_type_value = BackupSourceSnapshotDirectory.PathType.UNKNOWN
    normalized_kopia_snapshot_id = normalize_kopia_snapshot_id(kopia_snapshot_id)
    normalized_stats = stats if isinstance(stats, dict) else {}
    new_original_content_bytes = _optional_nonnegative_stat(
        normalized_stats,
        "new_original_content_bytes",
    )
    new_packed_content_bytes = _optional_nonnegative_stat(
        normalized_stats,
        "new_packed_content_bytes",
    )
    if status_value == BackupSourceSnapshotDirectory.Status.AVAILABLE and not normalized_kopia_snapshot_id:
        raise ValidationError({"kopia_snapshot_id": "Available directory results require kopia_snapshot_id."})
    if (
        status_value == BackupSourceSnapshotDirectory.Status.FAILED
        and not str(error_code or "").strip()
        and not str(error_message or "").strip()
    ):
        raise ValidationError({"error_message": "Failed directory results require an error message."})

    row, _created = BackupSourceSnapshotDirectory.objects.update_or_create(
        source_snapshot=source_snapshot,
        backup_config_dir_id=int(backup_config_dir_id),
        defaults={
            "organization_id": source_snapshot.organization_id,
            "backup_config_id": source_snapshot.backup_config_id,
            "source_path": str(source_path).strip(),
            "path_type": path_type_value,
            "display_name": str(display_name or "").strip(),
            "repository_id": int(repository_id),
            "kopia_snapshot_id": normalized_kopia_snapshot_id or None,
            "status": status_value,
            "size_bytes": max(0, int(size_bytes or 0)),
            "new_original_content_bytes": new_original_content_bytes,
            "new_packed_content_bytes": new_packed_content_bytes,
            "file_count": max(0, int(file_count or 0)),
            "dir_count": max(0, int(dir_count or 0)),
            "stats": normalized_stats,
            "error_code": str(error_code or "").strip(),
            "error_message": str(error_message or "").strip(),
        },
    )
    refresh_source_snapshot_summary(source_snapshot=source_snapshot)
    return row


@transaction.atomic
def refresh_source_snapshot_summary(
    *,
    source_snapshot: BackupSourceSnapshot,
    finished_at=None,
) -> BackupSourceSnapshot:
    rows = list(
        BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot_id=source_snapshot.id,
        )
    )
    total = max(int(source_snapshot.directory_count or 0), len(rows))
    success = sum(1 for row in rows if row.status == BackupSourceSnapshotDirectory.Status.AVAILABLE)
    failed = sum(
        1
        for row in rows
        if row.status
        in {
            BackupSourceSnapshotDirectory.Status.FAILED,
            BackupSourceSnapshotDirectory.Status.CANCELLED,
        }
    )
    completed = sum(1 for row in rows if row.status in _TERMINAL_DIRECTORY_STATUSES)

    source_snapshot.directory_count = total
    source_snapshot.successful_directory_count = success
    source_snapshot.failed_directory_count = failed
    source_snapshot.total_size_bytes = sum(
        int(row.size_bytes or 0)
        for row in rows
        if row.status == BackupSourceSnapshotDirectory.Status.AVAILABLE
    )
    source_snapshot.file_count = sum(int(row.file_count or 0) for row in rows if row.status == BackupSourceSnapshotDirectory.Status.AVAILABLE)
    source_snapshot.dir_count = sum(int(row.dir_count or 0) for row in rows if row.status == BackupSourceSnapshotDirectory.Status.AVAILABLE)

    if total > 0 and completed >= total:
        if success == total:
            source_snapshot.status = BackupSourceSnapshot.Status.AVAILABLE
            source_snapshot.error_code = ""
            source_snapshot.error_message = ""
        elif success > 0:
            source_snapshot.status = BackupSourceSnapshot.Status.PARTIAL
        else:
            source_snapshot.status = BackupSourceSnapshot.Status.FAILED
        source_snapshot.finished_at = finished_at or source_snapshot.finished_at or timezone.now()
    elif source_snapshot.status not in {
        BackupSourceSnapshot.Status.FAILED,
        BackupSourceSnapshot.Status.DELETING,
        BackupSourceSnapshot.Status.DELETE_FAILED,
        BackupSourceSnapshot.Status.DELETED,
    }:
        source_snapshot.status = BackupSourceSnapshot.Status.CREATING
        source_snapshot.finished_at = None

    source_snapshot.save(
        update_fields=[
            "status",
            "directory_count",
            "successful_directory_count",
            "failed_directory_count",
            "total_size_bytes",
            "file_count",
            "dir_count",
            "error_code",
            "error_message",
            "finished_at",
            "updated_at",
        ]
    )
    return source_snapshot


@transaction.atomic
def mark_source_snapshot_failed(
    *,
    source_snapshot: BackupSourceSnapshot,
    error_code: str,
    error_message: str,
    finished_at=None,
) -> BackupSourceSnapshot:
    source_snapshot.status = BackupSourceSnapshot.Status.FAILED
    source_snapshot.error_code = str(error_code or "").strip()
    source_snapshot.error_message = str(error_message or "").strip()
    source_snapshot.finished_at = finished_at or timezone.now()
    source_snapshot.save(
        update_fields=[
            "status",
            "error_code",
            "error_message",
            "finished_at",
            "updated_at",
        ]
    )
    return refresh_source_snapshot_summary(
        source_snapshot=source_snapshot,
        finished_at=source_snapshot.finished_at,
    )


@transaction.atomic
def soft_delete_source_snapshot(*, source_snapshot: BackupSourceSnapshot) -> BackupSourceSnapshot:
    source_snapshot.status = BackupSourceSnapshot.Status.DELETED
    source_snapshot.deleted_at = timezone.now()
    source_snapshot.save(update_fields=["status", "deleted_at", "updated_at"])
    return source_snapshot


def backup_config_directories(*, backup_config_id: int, organization_id: int) -> list[BackupConfigDirectory]:
    return list(
        BackupConfigDirectory.objects.filter(
            organization_id=organization_id,
            backup_config_id=backup_config_id,
        ).order_by("sort_order", "id")
    )


def snapshot_config_directories(
    *, source_snapshot: BackupSourceSnapshot
) -> list[SnapshotDirectoryConfig]:
    metadata = source_snapshot.metadata if isinstance(source_snapshot.metadata, dict) else {}
    values = metadata.get("config_directories")
    if not isinstance(values, list):
        return []
    directories: list[SnapshotDirectoryConfig] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        try:
            directory_id = int(value.get("id") or 0)
        except (TypeError, ValueError):
            continue
        path = str(value.get("path") or "").strip()
        if directory_id <= 0 or not path:
            continue
        directories.append(
            SnapshotDirectoryConfig(
                id=directory_id,
                path=path,
                path_type=str(value.get("path_type") or "unknown"),
                display_name=str(value.get("display_name") or ""),
            )
        )
    return directories


def get_backup_config_for_snapshot(*, source_snapshot: BackupSourceSnapshot) -> BackupConfig:
    config = BackupConfig.objects.filter(
        organization_id=source_snapshot.organization_id,
        id=source_snapshot.backup_config_id,
    ).first()
    if config is None:
        raise BackupConfig.DoesNotExist("Backup config not found for snapshot.")
    return config


def recoverable_snapshot_directories(
    *,
    source_snapshot: BackupSourceSnapshot,
) -> list[BackupSourceSnapshotDirectory]:
    if source_snapshot.status not in {
        BackupSourceSnapshot.Status.AVAILABLE,
        BackupSourceSnapshot.Status.PARTIAL,
    }:
        return []
    return list(
        source_snapshot.directories.filter(
            status=BackupSourceSnapshotDirectory.Status.AVAILABLE,
        ).exclude(kopia_snapshot_id__isnull=True)
    )
