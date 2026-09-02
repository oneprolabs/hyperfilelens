"""Step 3 backup/restore column progress (independent from task.progress)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from django.utils import timezone

from apps.protection.models import BackupConfig, BackupConfigDirectory, BackupSourceSnapshot

logger = logging.getLogger(__name__)

_RESTORE_RESULT_COUNTS = re.compile(
    r"(?i)^Restored\s+(\d+)\s+files?,\s+(\d+)\s+director(?:y|ies)\s+and\s+(\d+)\s+symbolic\s+links?"
)

_STABILITY_TIERS: tuple[tuple[int, int, int], ...] = (
    (500 * 1024 * 1024, 45, 6),
    (50 * 1024 * 1024 * 1024, 120, 15),
)
_DEFAULT_STABILITY_SECONDS = 90
_DEFAULT_STABILITY_SAMPLES = 12
_MAX_RELATIVE_JUMP = 0.10
_MAX_ADJACENT_DELTA = 0.03


def _int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def du_total_for_backup_config(config: BackupConfig) -> int:
    total = 0
    for directory in config.directories.all():
        total += max(0, int(directory.estimated_size_bytes or 0))
    return total


def du_total_for_task(
    *,
    task,
    source_snapshot: BackupSourceSnapshot | None = None,
) -> int:
    result_payload = task.result_payload if isinstance(task.result_payload, dict) else {}
    if result_payload.get("du_total_known"):
        return _int(result_payload.get("du_total"))
    request_payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    if request_payload.get("du_total_known"):
        return _int(request_payload.get("du_total"))
    transfer = result_payload.get("transfer_progress")
    if isinstance(transfer, dict):
        stored = _int(transfer.get("du_total"))
        if stored > 0:
            return stored

    config = _backup_config_for_task(task=task, source_snapshot=source_snapshot)
    if config is None:
        return _int(result_payload.get("du_total"))

    if request_payload.get("directory_size_refresh_required"):
        directories = list(config.directories.all())
        task_created_at = getattr(task, "created_at", None)
        if not directories or task_created_at is None:
            return 0
        if any(
            directory.size_estimated_at is None
            or directory.size_estimated_at < task_created_at
            for directory in directories
        ):
            return 0
    return du_total_for_backup_config(config)


def _backup_config_for_task(
    *,
    task,
    source_snapshot: BackupSourceSnapshot | None = None,
) -> BackupConfig | None:
    if source_snapshot is not None and source_snapshot.backup_config_id:
        return BackupConfig.objects.filter(
            organization_id=task.organization_id,
            id=source_snapshot.backup_config_id,
        ).prefetch_related("directories").first()
    request_payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    config_id = _int(request_payload.get("backup_config_id"))
    if config_id <= 0:
        return None
    return BackupConfig.objects.filter(
        organization_id=task.organization_id,
        id=config_id,
    ).prefetch_related("directories").first()


def stability_window_params(estimated_bytes: int) -> tuple[int, int]:
    seconds = _DEFAULT_STABILITY_SECONDS
    samples = _DEFAULT_STABILITY_SAMPLES
    for threshold, tier_seconds, tier_samples in _STABILITY_TIERS:
        if estimated_bytes >= threshold:
            seconds = tier_seconds
            samples = tier_samples
    return seconds, samples


def _parse_history(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    history: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        at_raw = str(item.get("at") or "").strip()
        estimated = _int(item.get("estimated_bytes"))
        if not at_raw or estimated <= 0:
            continue
        history.append({"at": at_raw, "estimated_bytes": estimated})
    return history


def _append_estimated_sample(
    *,
    history: list[dict[str, Any]],
    estimated_bytes: int,
    now: datetime,
) -> list[dict[str, Any]]:
    if estimated_bytes <= 0:
        return history
    iso = now.isoformat()
    if history and history[-1].get("estimated_bytes") == estimated_bytes:
        return history
    updated = list(history)
    updated.append({"at": iso, "estimated_bytes": estimated_bytes})
    cutoff = now - timedelta(minutes=10)
    pruned: list[dict[str, Any]] = []
    for item in updated:
        at_raw = str(item.get("at") or "")
        try:
            at = datetime.fromisoformat(at_raw.replace("Z", "+00:00"))
            if timezone.is_naive(at):
                at = timezone.make_aware(at, timezone.get_current_timezone())
        except ValueError:
            continue
        if at >= cutoff:
            pruned.append(item)
    return pruned[-60:]


def _estimated_is_stable(*, history: list[dict[str, Any]], estimated_bytes: int, now: datetime) -> bool:
    if estimated_bytes <= 0 or len(history) < 2:
        return False
    window_seconds, min_samples = stability_window_params(estimated_bytes)
    window_start = now - timedelta(seconds=window_seconds)
    window_samples: list[dict[str, Any]] = []
    for item in history:
        at_raw = str(item.get("at") or "")
        try:
            at = datetime.fromisoformat(at_raw.replace("Z", "+00:00"))
            if timezone.is_naive(at):
                at = timezone.make_aware(at, timezone.get_current_timezone())
        except ValueError:
            continue
        if at >= window_start:
            window_samples.append(item)
    if len(window_samples) < min_samples:
        return False

    values = [_int(item.get("estimated_bytes")) for item in window_samples]
    if any(value <= 0 for value in values):
        return False
    if any(values[index] < values[index - 1] for index in range(1, len(values))):
        return False
    for index in range(1, len(values)):
        prev = values[index - 1]
        curr = values[index]
        if prev > 0 and abs(curr - prev) / prev > _MAX_ADJACENT_DELTA:
            return False
    oldest = values[0]
    newest = values[-1]
    if oldest > 0 and (newest - oldest) / oldest > _MAX_RELATIVE_JUMP:
        return False
    return newest == estimated_bytes


def should_latch_kopia_switch(
    *,
    uploaded_bytes: int,
    estimated_bytes: int,
    history: list[dict[str, Any]],
    now: datetime | None = None,
) -> bool:
    if uploaded_bytes <= 0 or estimated_bytes <= 0:
        return False
    if estimated_bytes < int(uploaded_bytes * 1.05):
        return False
    return _estimated_is_stable(history=history, estimated_bytes=estimated_bytes, now=now or timezone.now())


def compute_step3_display_percent(
    *,
    bytes_done: int,
    effective_total: int,
    previous_display: float | None,
) -> float | None:
    if effective_total <= 0:
        return previous_display
    raw = min(100.0, max(0.0, 100.0 * bytes_done / effective_total))
    if previous_display is not None and raw < previous_display:
        return previous_display
    return round(raw, 2)


def _safe_progress_total(*, bytes_done: int, candidate_total: int) -> int:
    """Keep a running progress denominator strictly ahead of its numerator."""
    if candidate_total <= 0 or bytes_done <= candidate_total:
        return candidate_total
    # A reference/estimate can be smaller than the logical bytes processed
    # (for example du -sk counts allocated blocks). Keep the row meaningful
    # without ever presenting 100% before the task reaches a terminal state.
    adjusted = (bytes_done * 100 + 98) // 99
    return max(candidate_total, adjusted, bytes_done + 1)


_MIN_STEP3_SPEED_BPS = 100


def _step3_upload_speed_bps(*, merged: dict[str, Any], aggregate: dict[str, Any]) -> int:
    for source in (aggregate, merged):
        speed = _int(source.get("upload_speed_bps"))
        if speed >= _MIN_STEP3_SPEED_BPS:
            return speed
    return 0


def _step3_processing_speed_bps(*, merged: dict[str, Any], aggregate: dict[str, Any]) -> int:
    for source in (aggregate, merged):
        speed = _int(source.get("processing_speed_bps") or source.get("hash_speed_bps"))
        if speed >= _MIN_STEP3_SPEED_BPS:
            return speed
    return 0


def compute_step3_eta_seconds(
    *,
    bytes_done: int,
    bytes_total: int,
    processing_speed_bps: int,
) -> int | None:
    if bytes_total <= 0 or processing_speed_bps < _MIN_STEP3_SPEED_BPS:
        return None
    remaining = int(bytes_total) - int(bytes_done)
    if remaining <= 0:
        return 0
    return max(1, int(remaining / processing_speed_bps))


def apply_step3_row3_metrics(
    *,
    merged: dict[str, Any],
    aggregate: dict[str, Any],
    bytes_done: int,
    effective_total: int,
    eta_speed_kind: str = "processing",
) -> None:
    """Keep displayed upload speed separate from the logical ETA byte domain."""
    phase = str(merged.get("phase") or "").lower()
    if phase != "transferring":
        if phase == "finalizing":
            merged["eta_seconds"] = None
            merged["eta_source"] = None
        return

    upload_speed_bps = _step3_upload_speed_bps(merged=merged, aggregate=aggregate)
    if upload_speed_bps > 0:
        merged["upload_speed_bps"] = upload_speed_bps
        merged["speed_bps"] = upload_speed_bps
        merged["speed_source"] = merged.get("upload_speed_source") or aggregate.get("speed_source") or "step3"

    eta_speed_bps = (
        upload_speed_bps
        if eta_speed_kind == "upload"
        else _step3_processing_speed_bps(merged=merged, aggregate=aggregate)
    )
    if effective_total <= 0 or not merged.get("bytes_total_known"):
        merged["eta_seconds"] = None
        merged["eta_source"] = None
        return

    if bytes_done <= 0:
        merged["eta_seconds"] = None
        merged["eta_source"] = None
        return

    if eta_speed_bps < _MIN_STEP3_SPEED_BPS:
        merged["eta_seconds"] = None
        merged["eta_source"] = None
        return

    eta_seconds = compute_step3_eta_seconds(
        bytes_done=bytes_done,
        bytes_total=effective_total,
        processing_speed_bps=eta_speed_bps,
    )
    if eta_seconds is not None:
        merged["eta_seconds"] = eta_seconds
        merged["eta_source"] = "step3"
    else:
        merged["eta_seconds"] = None
        merged["eta_source"] = None


def enrich_step3_backup_transfer(
    *,
    transfer: dict[str, Any],
    previous: dict[str, Any] | None,
    aggregate: dict[str, Any],
    du_total: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    merged = dict(transfer)
    prev = previous if isinstance(previous, dict) else {}
    current_now = now or timezone.now()

    # Agent reconnect snapshots can temporarily contain zeroed counters. Keep
    # task-scoped cumulative values monotonic instead of letting a transient
    # snapshot reset the Step 3 row while its display percent stays latched.
    schema_version = max(
        _int(aggregate.get("progress_schema_version")),
        _int(merged.get("progress_schema_version")),
        _int(prev.get("progress_schema_version")),
        1,
    )
    processed_bytes = max(
        _int(aggregate.get("processed_bytes")),
        _int(aggregate.get("bytes_done")),
        _int(merged.get("processed_bytes")),
        _int(merged.get("bytes_done")),
        _int(prev.get("processed_bytes")),
        _int(prev.get("bytes_done")),
    )
    uploaded_bytes = max(
        _int(aggregate.get("uploaded_bytes")),
        _int(merged.get("uploaded_bytes")),
        _int(prev.get("uploaded_bytes")),
    )
    if schema_version < 2:
        processed_bytes = max(processed_bytes, uploaded_bytes)
    uploaded_count = max(
        _int(aggregate.get("uploaded_count")),
        _int(merged.get("uploaded_count")),
        _int(prev.get("uploaded_count")),
    )
    hashed_count = max(
        _int(aggregate.get("hashed_count")),
        _int(merged.get("hashed_count")),
        _int(prev.get("hashed_count")),
    )
    estimated_bytes = max(
        _int(aggregate.get("estimated_bytes")),
        _int(merged.get("estimated_bytes")),
        _int(prev.get("estimated_bytes")),
    )

    merged["uploaded_bytes"] = uploaded_bytes
    merged["uploaded_count"] = uploaded_count
    merged["hashed_count"] = hashed_count
    merged["estimated_bytes"] = estimated_bytes
    frozen_du_total = _int(prev.get("du_total"))
    if frozen_du_total <= 0:
        frozen_du_total = du_total
    merged["du_total"] = frozen_du_total

    switch_latched = bool(prev.get("switch_latched"))
    kopia_total_locked = _int(prev.get("kopia_total_locked"))
    history = _parse_history(prev.get("estimated_history"))
    if estimated_bytes > 0:
        history = _append_estimated_sample(history=history, estimated_bytes=estimated_bytes, now=current_now)
    merged["estimated_history"] = history

    if schema_version < 2 and not switch_latched and should_latch_kopia_switch(
        uploaded_bytes=uploaded_bytes,
        estimated_bytes=estimated_bytes,
        history=history,
        now=current_now,
    ):
        switch_latched = True
        kopia_total_locked = estimated_bytes

    total_source = ""
    total_adjusted = False
    if schema_version >= 2:
        # Schema-v2 reports the logical estimate independently from lane
        # completeness. Prefer Kopia's estimate, then use the preflight du
        # total as a clearly-marked reference when Kopia has no estimate yet.
        kopia_total_known = bool(aggregate.get("bytes_total_known"))
        kopia_total = _int(aggregate.get("bytes_total")) if kopia_total_known else estimated_bytes
        previous_total = max(
            _int(prev.get("bytes_total")),
            _int(prev.get("kopia_total_locked")),
        )
        if kopia_total > 0:
            # A du fallback is only a temporary reference. As soon as Kopia
            # reports a positive logical estimate, do not let a larger du
            # value keep overriding it while claiming the source is Kopia.
            previous_kopia_total = (
                previous_total
                if str(prev.get("bytes_total_source") or "").lower() == "kopia"
                and not bool(prev.get("bytes_total_adjusted"))
                else 0
            )
            effective_total = max(kopia_total, previous_kopia_total)
            total_source = "kopia"
        else:
            effective_total = max(_int(du_total), previous_total)
            total_source = "du_reference" if effective_total > 0 else ""
        safe_total = _safe_progress_total(
            bytes_done=processed_bytes,
            candidate_total=effective_total,
        )
        total_adjusted = safe_total > effective_total
        effective_total = safe_total
        switch_latched = effective_total > 0
        kopia_total_locked = effective_total
        merged["bytes_total_estimated"] = total_source != "kopia" or not kopia_total_known
    elif switch_latched:
        effective_total = estimated_bytes if estimated_bytes > 0 else kopia_total_locked
        if not bool(aggregate.get("bytes_total_known")) and kopia_total_locked > 0:
            effective_total = kopia_total_locked
        merged["bytes_total_estimated"] = False
    else:
        effective_total = frozen_du_total
        merged["bytes_total_estimated"] = frozen_du_total > 0

    merged["switch_latched"] = switch_latched
    merged["kopia_total_locked"] = kopia_total_locked
    merged["progress_schema_version"] = schema_version
    merged["processed_bytes"] = processed_bytes
    merged["bytes_done"] = processed_bytes
    if effective_total > 0:
        merged["bytes_total"] = effective_total
        merged["bytes_total_known"] = True
        merged["bytes_total_reference"] = total_source == "du_reference" or total_adjusted
        merged["bytes_total_source"] = total_source or "unknown"
        merged["bytes_total_adjusted"] = total_adjusted
    else:
        merged.pop("bytes_total", None)
        merged["bytes_total_known"] = False
        merged["bytes_total_reference"] = False
        merged.pop("bytes_total_source", None)
        merged.pop("bytes_total_adjusted", None)

    phase = str(merged.get("phase") or "").lower()
    if schema_version >= 2 and effective_total <= 0:
        merged.pop("step3_display_percent", None)
        display = None
    else:
        prev_display = None
        if schema_version < 2:
            prev_display = _float(prev.get("step3_display_percent"))
            if prev_display is None:
                prev_display = _float(prev.get("display_percent"))
        display = compute_step3_display_percent(
            bytes_done=processed_bytes,
            effective_total=effective_total,
            previous_display=prev_display,
        )
    if phase not in {"done", "success", "completed"} and display is not None and display >= 100:
        display = 99.0
    if display is not None:
        merged["step3_display_percent"] = display

    apply_step3_row3_metrics(
        merged=merged,
        aggregate=aggregate,
        bytes_done=processed_bytes,
        effective_total=effective_total if effective_total > 0 and merged.get("bytes_total_known") else 0,
    )
    if phase == "transferring":
        merged["show_metrics"] = bool(
            processed_bytes > 0
            or merged.get("upload_speed_bps")
            or merged.get("eta_seconds")
            or effective_total > 0
        )

    logger.debug(
        "step3_backup_progress processed_bytes=%s uploaded_bytes=%s estimated_bytes=%s du_total=%s "
        "switch_latched=%s kopia_total_locked=%s effective_total=%s step3_display_percent=%s "
        "upload_speed_bps=%s eta_seconds=%s eta_source=%s",
        processed_bytes,
        uploaded_bytes,
        estimated_bytes,
        du_total,
        switch_latched,
        kopia_total_locked,
        effective_total,
        merged.get("step3_display_percent"),
        merged.get("upload_speed_bps"),
        merged.get("eta_seconds"),
        merged.get("eta_source"),
    )
    return merged


def restore_snapshot_bytes_total(record) -> int:
    from apps.protection.models import BackupSourceSnapshotDirectory

    items = list(record.items.all())
    directory_ids = [item.source_snapshot_directory_id for item in items if item.source_snapshot_directory_id]
    if not directory_ids:
        return 0
    directories = BackupSourceSnapshotDirectory.objects.filter(
        organization_id=record.organization_id,
        id__in=directory_ids,
    )
    return sum(max(0, int(directory.size_bytes or 0)) for directory in directories)


def restore_file_count_seed(record) -> int:
    from apps.protection.models import BackupSourceSnapshotDirectory

    items = list(record.items.all())
    directory_ids = [item.source_snapshot_directory_id for item in items if item.source_snapshot_directory_id]
    if not directory_ids:
        return 0
    directories = BackupSourceSnapshotDirectory.objects.filter(
        organization_id=record.organization_id,
        id__in=directory_ids,
    )
    return sum(max(0, int(directory.file_count or 0)) for directory in directories)


def restore_terminal_counts(record) -> dict[str, int] | None:
    """Return final file/dir/symlink counts reported by every restore lane."""
    totals = {"files": 0, "directories": 0, "symlinks": 0}
    found = False
    for item in record.items.all():
        payload = item.result_payload if isinstance(item.result_payload, dict) else {}
        for entry in payload.get("restore_results") or []:
            result = entry.get("result") if isinstance(entry, dict) else None
            if not isinstance(result, dict):
                continue
            output = result.get("stderr_tail") or result.get("stderr") or ""
            if not isinstance(output, str):
                continue
            match = next(
                (
                    parsed
                    for line in reversed(output.splitlines())
                    if (parsed := _RESTORE_RESULT_COUNTS.match(line.strip()))
                ),
                None,
            )
            if match:
                found = True
                totals["files"] += int(match.group(1))
                totals["directories"] += int(match.group(2))
                totals["symlinks"] += int(match.group(3))
    return totals if found else None


def enrich_step3_restore_transfer(
    *,
    transfer: dict[str, Any],
    previous: dict[str, Any] | None,
    aggregate: dict[str, Any],
    bytes_total: int,
    file_count_seed: int = 0,
) -> dict[str, Any]:
    merged = dict(transfer)
    prev = previous if isinstance(previous, dict) else {}

    bytes_done = max(
        _int(aggregate.get("bytes_done")),
        _int(merged.get("bytes_done")),
        _int(prev.get("bytes_done")),
    )
    processed_count = _int(aggregate.get("processed_count"))
    total_count = _int(aggregate.get("total_count"))
    prev_processed = _int(prev.get("processed_count"))
    prev_total = _int(prev.get("total_count"))
    processed_count = max(processed_count, prev_processed)
    total_count = max(total_count, prev_total, file_count_seed, processed_count)
    path_index = aggregate.get("path_index")
    path_total = aggregate.get("path_total")
    if path_index is None:
        path_index = prev.get("path_index")
    if path_total is None:
        path_total = prev.get("path_total")

    merged["processed_count"] = processed_count
    merged["total_count"] = total_count
    if path_index is not None:
        merged["path_index"] = _int(path_index) or None
    if path_total is not None:
        merged["path_total"] = _int(path_total) or None

    effective_total = bytes_total if bytes_total > 0 else _int(merged.get("bytes_total"))
    merged["bytes_done"] = bytes_done
    if effective_total > 0:
        merged["bytes_total"] = effective_total
        merged["bytes_total_known"] = True
        merged["bytes_total_reference"] = False
        merged["bytes_total_estimated"] = False

    if str(merged.get("phase") or "").lower() in {"done", "success", "completed"} and effective_total > 0:
        merged["bytes_done"] = effective_total
        merged["processed_bytes"] = effective_total
        bytes_done = effective_total

    prev_display = _float(prev.get("step3_display_percent"))
    if prev_display is None:
        prev_display = _float(prev.get("display_percent"))
    display = compute_step3_display_percent(
        bytes_done=bytes_done,
        effective_total=effective_total,
        previous_display=prev_display,
    )
    if display is not None:
        merged["step3_display_percent"] = display

    phase = str(merged.get("phase") or "").lower()
    if phase == "transferring":
        merged["label_key"] = "protection.taskProgress.restore.itemsPath"
        merged["label_args"] = {
            "done": processed_count,
            "total": max(total_count, processed_count, 1),
            "i": int(merged.get("path_index") or 1),
            "m": int(merged.get("path_total") or 1),
        }
    apply_step3_row3_metrics(
        merged=merged,
        aggregate=aggregate,
        bytes_done=bytes_done,
        effective_total=effective_total if effective_total > 0 and merged.get("bytes_total_known") else 0,
        eta_speed_kind="upload",
    )
    if phase == "transferring":
        merged["show_metrics"] = bool(
            bytes_done > 0
            or merged.get("upload_speed_bps")
            or merged.get("speed_bps")
            or merged.get("eta_seconds")
            or effective_total > 0
        )

    logger.debug(
        "step3_restore_progress processed_count=%s total_count=%s path_index=%s path_total=%s "
        "bytes_done=%s bytes_total=%s step3_display_percent=%s upload_speed_bps=%s eta_seconds=%s",
        processed_count,
        total_count,
        merged.get("path_index"),
        merged.get("path_total"),
        bytes_done,
        effective_total,
        merged.get("step3_display_percent"),
        merged.get("upload_speed_bps"),
        merged.get("eta_seconds"),
    )
    return merged


def missing_directory_estimates(config: BackupConfig) -> list[BackupConfigDirectory]:
    return [
        directory
        for directory in config.directories.all()
        if (
            max(0, int(directory.estimated_size_bytes or 0)) <= 0
            and directory.size_estimated_at is None
        )
    ]
