from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.node.models import NodeTask
from apps.protection import conf as protection_conf
from apps.protection.models import BackupConfigDirectory, BackupSourceSnapshot, BackupSourceSnapshotDirectory
from apps.task.models import Task
from apps.protection.services.progress.aggregator import aggregate_lanes
from apps.protection.services.progress.display import enrich_kopia_progress_payload
from apps.protection.services.progress.orchestrated_progress import merge_transfer_progress, persist_task_progress, slim_transfer_progress
from apps.protection.services.progress.kopia_fields import is_orchestration_progress, normalize_lane_progress
from apps.protection.services.progress.lane_sampler import apply_speed_and_eta
from apps.protection.services.progress.orchestration_label import backup_orchestration_label_meta
from apps.protection.services.progress.step3_progress import du_total_for_task, enrich_step3_backup_transfer

_DIRECTORY_DONE = {
    BackupSourceSnapshotDirectory.Status.AVAILABLE,
}
_DIRECTORY_ACTIVE = {
    BackupSourceSnapshotDirectory.Status.PENDING,
    BackupSourceSnapshotDirectory.Status.DISPATCHING,
    BackupSourceSnapshotDirectory.Status.RUNNING,
    BackupSourceSnapshotDirectory.Status.CREATING,
}

_SUBSTANTIVE_PROGRESS_FIELDS = (
    "hashed_bytes",
    "uploaded_bytes",
    "hashed_count",
    "uploaded_count",
    "hashing_count",
    "kopia_percent",
    "percent",
)
_SUBSTANTIVE_PROGRESS_PHASES = {
    "hashing",
    "uploading",
    "snapshot_created",
    "repository_ready",
    "snapshot_start",
}


def _has_substantive_progress_change(
    *, current: dict[str, Any] | None, previous: dict[str, Any] | None
) -> bool:
    if not isinstance(current, dict) or not current:
        return False
    previous = previous if isinstance(previous, dict) else {}
    for key in _SUBSTANTIVE_PROGRESS_FIELDS:
        value = current.get(key)
        if value not in (None, "") and value != previous.get(key):
            return True
    phase = str(current.get("kopia_phase") or current.get("phase") or "").strip().lower()
    previous_phase = str(
        previous.get("kopia_phase") or previous.get("phase") or ""
    ).strip().lower()
    return phase in _SUBSTANTIVE_PROGRESS_PHASES and phase != previous_phase


def build_backup_kopia_progress(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot | None = None,
) -> dict[str, Any]:
    directories = _directories_for_task(task=task, source_snapshot=source_snapshot)
    lane_rows: list[dict[str, Any]] = []
    for directory in directories:
        lane_rows.append(_lane_from_directory(directory))

    aggregate = aggregate_lanes(lane_rows)
    label_meta, phase = backup_orchestration_label_meta(
        task_status=str(task.status or "").lower(),
        lanes=lane_rows,
        aggregate=aggregate,
        current_step=str(task.current_step or ""),
    )
    payload = {
        "orchestration_label": "",
        "orchestration_label_key": label_meta.get("label_key"),
        "orchestration_label_args": label_meta.get("label_args") or {},
        "orchestration_phase": phase,
        "aggregate": aggregate,
        "lanes": [_public_lane(row) for row in lane_rows],
    }
    result_payload = task.result_payload if isinstance(task.result_payload, dict) else {}
    previous_transfer = result_payload.get("transfer_progress") if isinstance(result_payload.get("transfer_progress"), dict) else {}
    transfer = merge_transfer_progress(previous=previous_transfer, current=slim_transfer_progress(payload))
    enriched = enrich_kopia_progress_payload(payload, transfer_progress=transfer)
    transfer = merge_transfer_progress(
        previous=previous_transfer,
        current=slim_transfer_progress(enriched),
    )
    transfer = enrich_step3_backup_transfer(
        transfer=transfer,
        previous=previous_transfer,
        aggregate=aggregate,
        du_total=du_total_for_task(task=task, source_snapshot=source_snapshot),
    )
    enriched["transfer_progress"] = transfer
    return enriched


def sync_backup_directory_progress_from_node_task(*, node_task: NodeTask) -> bool:
    """Lightweight hot-path sync: directory snapshot + task percent from NodeTask progress."""
    if node_task.correlation_type != protection_conf.PROTECTION_BACKUP_CORRELATION_TYPE or not node_task.correlation_id:
        return False
    progress = _node_task_progress(node_task)
    if not progress:
        return False
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=node_task.organization_id,
        task_uuid=node_task.correlation_id,
    ).first()
    if snapshot is None:
        return False
    payload = node_task.payload if isinstance(node_task.payload, dict) else {}
    backup_config_dir_id = 0
    try:
        backup_config_dir_id = int(payload.get("backup_config_dir_id") or 0)
    except (TypeError, ValueError):
        backup_config_dir_id = 0
    directory = None
    if backup_config_dir_id > 0:
        directory = BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=snapshot,
            backup_config_dir_id=backup_config_dir_id,
        ).first()
    if directory is None and node_task.id:
        directory = BackupSourceSnapshotDirectory.objects.filter(
            source_snapshot=snapshot,
            node_task_id=node_task.id,
        ).first()
    if directory is None:
        return False
    update_directory_progress_snapshot(
        directory=directory,
        progress=progress,
        node_task=node_task,
    )
    task = Task.objects.filter(
        organization_id=node_task.organization_id,
        task_uuid=node_task.correlation_id,
    ).first()
    if task is not None:
        sync_backup_task_progress(task=task, source_snapshot=snapshot)
    return True


def _node_task_progress(node_task: NodeTask) -> dict | None:
    result = node_task.result if isinstance(node_task.result, dict) else {}
    progress = result.get("last_progress")
    return progress if isinstance(progress, dict) else None


def sync_backup_task_progress(*, task: Task, source_snapshot: BackupSourceSnapshot | None = None) -> dict[str, Any]:
    payload = build_backup_kopia_progress(task=task, source_snapshot=source_snapshot)
    if task.status in {Task.Status.PENDING, Task.Status.RUNNING} or payload.get("orchestration_phase") == "done":
        payload = persist_task_progress(task=task, kopia_payload=payload, kind="backup")
    return payload


def update_directory_progress_snapshot(
    *,
    directory: BackupSourceSnapshotDirectory,
    progress: dict[str, Any] | None,
    node_task: NodeTask | None = None,
) -> BackupSourceSnapshotDirectory:
    previous_progress = (
        directory.last_progress_snapshot
        if isinstance(directory.last_progress_snapshot, dict)
        else {}
    )
    sample = directory.last_progress_sample if isinstance(directory.last_progress_sample, dict) else {}
    normalized = normalize_lane_progress(
        progress=progress,
        status=str(directory.status or "").lower(),
        frozen_bytes_done=int(directory.size_bytes or 0),
        frozen_bytes_total=int(directory.size_bytes or 0),
        reference_bytes_total=_reference_bytes_for_directory(directory),
    )
    if normalized.get("is_transfer"):
        normalized = apply_speed_and_eta(lane=normalized, sample=sample, persist_sample=True)
    update_fields = ["updated_at"]
    directory.last_progress_snapshot = {
        **(progress or {}),
        **{k: v for k, v in normalized.items() if k not in {"last_sample"}},
    }
    update_fields.append("last_progress_snapshot")
    if _has_substantive_progress_change(
        current=progress,
        previous=previous_progress,
    ):
        directory.last_substantive_progress_at = timezone.now()
        directory.stall_warned_at = None
        update_fields.extend(["last_substantive_progress_at", "stall_warned_at"])
    if normalized.get("last_sample"):
        directory.last_progress_sample = normalized["last_sample"]
        update_fields.append("last_progress_sample")
    if node_task is not None and node_task.id and directory.node_task_id != node_task.id:
        directory.node_task_id = node_task.id
        update_fields.append("node_task_id")
    directory.save(update_fields=list(dict.fromkeys(update_fields)))
    return directory


def _directories_for_task(
    *,
    task: Task,
    source_snapshot: BackupSourceSnapshot | None,
) -> list[BackupSourceSnapshotDirectory]:
    if source_snapshot is not None:
        return list(source_snapshot.directories.order_by("id"))
    snapshot = BackupSourceSnapshot.objects.filter(
        organization_id=task.organization_id,
        task_uuid=task.task_uuid,
    ).first()
    if snapshot is None:
        return []
    return list(snapshot.directories.order_by("id"))


def _lane_from_directory(directory: BackupSourceSnapshotDirectory) -> dict[str, Any]:
    raw = directory.last_progress_snapshot if isinstance(directory.last_progress_snapshot, dict) else {}
    status = str(directory.status or "").lower()
    if directory.status in _DIRECTORY_DONE:
        status = "success"
    sample = directory.last_progress_sample if isinstance(directory.last_progress_sample, dict) else {}
    normalized = normalize_lane_progress(
        progress=raw,
        status=status,
        frozen_bytes_done=int(directory.size_bytes or 0),
        frozen_bytes_total=int(directory.size_bytes or 0),
        reference_bytes_total=_reference_bytes_for_directory(directory),
    )
    if normalized.get("is_transfer") or status in {s.value for s in _DIRECTORY_ACTIVE}:
        normalized = apply_speed_and_eta(lane=normalized, sample=sample, persist_sample=False)
    return {
        "id": str(directory.id),
        "name": directory.display_name or directory.source_path,
        "status": status,
        "progress": normalized,
    }


def _public_lane(row: dict[str, Any]) -> dict[str, Any]:
    progress = row.get("progress") or {}
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "status": row.get("status"),
        "kopia_phase": progress.get("kopia_phase"),
        "progress_schema_version": progress.get("progress_schema_version"),
        "bytes_done": progress.get("bytes_done"),
        "processed_bytes": progress.get("processed_bytes"),
        "bytes_total": progress.get("bytes_total"),
        "bytes_total_known": progress.get("bytes_total_known"),
        "percent": progress.get("percent"),
        "speed_bps": progress.get("speed_bps"),
        "processing_speed_bps": progress.get("processing_speed_bps"),
        "hash_speed_bps": progress.get("hash_speed_bps"),
        "upload_speed_bps": progress.get("upload_speed_bps"),
        "eta_seconds": progress.get("eta_seconds"),
        "kopia_eta_seconds": progress.get("kopia_eta_seconds"),
        "progress_text": progress.get("progress_text"),
        "path_index": progress.get("path_index"),
        "path_total": progress.get("path_total"),
        "percent_source": progress.get("percent_source"),
        "orchestration_label": progress.get("orchestration_label"),
    }


def _reference_bytes_for_directory(directory: BackupSourceSnapshotDirectory) -> int:
    config_dir_id = int(directory.backup_config_dir_id or 0)
    if config_dir_id <= 0:
        return 0
    config_dir = BackupConfigDirectory.objects.filter(
        id=config_dir_id,
        organization_id=directory.organization_id,
    ).only("estimated_size_bytes").first()
    if config_dir is None:
        return 0
    return max(0, int(config_dir.estimated_size_bytes or 0))


def orchestration_label_from_progress(progress: dict[str, Any] | None) -> str:
    if not isinstance(progress, dict):
        return ""
    if is_orchestration_progress(progress):
        label = str(progress.get("orchestration_label") or "").strip()
        if label:
            return label
        phase = str(progress.get("kopia_phase") or progress.get("orchestration_phase") or "").strip()
        mapping = {
            "repository_prepare": "Connecting to the backup repository...",
            "repository_ready": "Backup repository is ready...",
            "snapshot_start": "Starting snapshot creation...",
        }
        return mapping.get(phase, "")
    return ""
