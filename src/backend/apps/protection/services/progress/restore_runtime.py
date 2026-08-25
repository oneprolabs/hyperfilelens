from __future__ import annotations

from typing import Any

from apps.node.models import NodeTask
from apps.protection.models import BackupSourceSnapshotDirectory
from apps.protection.services.progress.aggregator import aggregate_lanes
from apps.protection.services.progress.display import enrich_kopia_progress_payload
from apps.protection.services.progress.orchestrated_progress import merge_transfer_progress, persist_task_progress, slim_transfer_progress
from apps.protection.services.progress.kopia_fields import normalize_lane_progress
from apps.protection.services.progress.lane_sampler import apply_speed_and_eta
from apps.protection.services.progress.orchestration_label import restore_orchestration_label_meta
from apps.protection.services.progress.step3_progress import (
    enrich_step3_restore_transfer,
    restore_file_count_seed,
    restore_snapshot_bytes_total,
    restore_terminal_counts,
)
from apps.restore.models import RestoreRecord, RestoreRecordItem
from apps.task.models import Task

_ITEM_DONE = {RestoreRecordItem.Status.SUCCESS}
_ITEM_ACTIVE = {
    RestoreRecordItem.Status.PENDING,
    RestoreRecordItem.Status.RUNNING,
}


def build_restore_kopia_progress(*, record: RestoreRecord, task: Task | None = None) -> dict[str, Any]:
    if task is None:
        task = Task.objects.filter(
            organization_id=record.organization_id,
            task_uuid=record.task_uuid,
        ).first()
    items = list(record.items.order_by("id"))
    snapshot_directories = {
        directory.id: directory
        for directory in BackupSourceSnapshotDirectory.objects.filter(
            organization_id=record.organization_id,
            id__in=[item.source_snapshot_directory_id for item in items],
        )
    }
    lane_rows = [
        _lane_from_item(
            item,
            snapshot_directory=snapshot_directories.get(item.source_snapshot_directory_id),
        )
        for item in items
    ]
    aggregate = aggregate_lanes(lane_rows)
    task_status = str(task.status or "").lower() if task is not None else "running"
    label_meta, phase = restore_orchestration_label_meta(
        task_status=task_status,
        lanes=lane_rows,
        aggregate=aggregate,
        current_step=str(task.current_step or "") if task is not None else "restore",
    )
    payload = {
        "orchestration_label": "",
        "orchestration_label_key": label_meta.get("label_key"),
        "orchestration_label_args": label_meta.get("label_args") or {},
        "orchestration_phase": phase,
        "aggregate": aggregate,
        "lanes": [_public_lane(row) for row in lane_rows],
    }
    previous_transfer = {}
    if task is not None:
        result_payload = task.result_payload if isinstance(task.result_payload, dict) else {}
        previous_transfer = result_payload.get("transfer_progress") if isinstance(result_payload.get("transfer_progress"), dict) else {}
    transfer = merge_transfer_progress(previous=previous_transfer, current=slim_transfer_progress(payload))
    enriched = enrich_kopia_progress_payload(payload, transfer_progress=transfer)
    transfer = merge_transfer_progress(
        previous=previous_transfer,
        current=slim_transfer_progress(enriched),
    )
    transfer = enrich_step3_restore_transfer(
        transfer=transfer,
        previous=previous_transfer,
        aggregate=aggregate,
        bytes_total=restore_snapshot_bytes_total(record),
        file_count_seed=restore_file_count_seed(record),
    )
    terminal_counts = restore_terminal_counts(record)
    if transfer.get("phase") in {"done", "success", "completed"} and terminal_counts is not None:
        transfer.update(
            restored_file_count=terminal_counts["files"],
            restored_directory_count=terminal_counts["directories"],
            restored_symlink_count=terminal_counts["symlinks"],
        )
    enriched["transfer_progress"] = transfer
    return enriched


def sync_restore_task_progress(*, record: RestoreRecord, task: Task | None = None) -> dict[str, Any]:
    from apps.restore.services.restore_progress import sync_restore_items_from_node_tasks

    if task is None:
        task = Task.objects.filter(
            organization_id=record.organization_id,
            task_uuid=record.task_uuid,
        ).first()
    sync_restore_items_from_node_tasks(record=record)
    payload = build_restore_kopia_progress(record=record, task=task)
    if task is None:
        return payload
    if task.status in {Task.Status.PENDING, Task.Status.RUNNING} or payload.get("orchestration_phase") == "done":
        payload = persist_task_progress(task=task, kopia_payload=payload, kind="restore", restore_record=record)
    return payload


def update_restore_item_progress_snapshot(
    *,
    item: RestoreRecordItem,
    progress: dict[str, Any] | None,
    node_task: NodeTask | None = None,
) -> RestoreRecordItem:
    sample = item.last_progress_sample if isinstance(item.last_progress_sample, dict) else {}
    status = str(item.status or "").lower()
    normalized = normalize_lane_progress(progress=progress, status=status)
    if normalized.get("is_transfer"):
        normalized = apply_speed_and_eta(lane=normalized, sample=sample, persist_sample=True)
    update_fields = ["updated_at"]
    item.last_progress_snapshot = {
        **(progress or {}),
        **{k: v for k, v in normalized.items() if k not in {"last_sample"}},
    }
    update_fields.append("last_progress_snapshot")
    if normalized.get("last_sample"):
        item.last_progress_sample = normalized["last_sample"]
        update_fields.append("last_progress_sample")
    if node_task is not None and node_task.id:
        if item.node_task_id != node_task.id:
            item.node_task_id = node_task.id
            update_fields.append("node_task_id")
        if item.status == RestoreRecordItem.Status.PENDING:
            item.status = RestoreRecordItem.Status.RUNNING
            update_fields.append("status")
    item.save(update_fields=list(dict.fromkeys(update_fields)))
    return item


def _lane_from_item(
    item: RestoreRecordItem,
    *,
    snapshot_directory: BackupSourceSnapshotDirectory | None = None,
) -> dict[str, Any]:
    raw = item.last_progress_snapshot if isinstance(item.last_progress_snapshot, dict) else {}
    status = str(item.status or "").lower()
    if item.status in _ITEM_DONE:
        status = "success"
    sample = item.last_progress_sample if isinstance(item.last_progress_sample, dict) else {}
    normalized = normalize_lane_progress(progress=raw, status=status)
    snapshot_total = int(snapshot_directory.size_bytes or 0) if snapshot_directory is not None else 0
    if snapshot_total > 0 and status in {s.value for s in _ITEM_ACTIVE}:
        lane_total = int(normalized.get("bytes_total") or 0) if normalized.get("bytes_total_known") else 0
        if not normalized.get("bytes_total_known") or lane_total < snapshot_total:
            normalized["bytes_total"] = snapshot_total
            normalized["bytes_total_known"] = True
        # Snapshot size is the authoritative restore total, not a pre-estimate.
        normalized["bytes_total_reference"] = False
    if normalized.get("is_transfer") or status in {s.value for s in _ITEM_ACTIVE}:
        normalized = apply_speed_and_eta(lane=normalized, sample=sample, persist_sample=False)
    name = item.source_path or item.target_path
    return {
        "id": str(item.id),
        "name": name,
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
