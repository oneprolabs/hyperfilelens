from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from django.utils import timezone

from apps.protection.services.progress.creep import progress_creep
from apps.protection.services.progress.display import enrich_kopia_progress_payload
from apps.task.models import Task

TaskKind = Literal["backup", "restore"]

# Backup task.progress budget (0–100).
BACKUP_LOGIC_END = 2.0
BACKUP_PREPARE_END = 5.0
BACKUP_ESTIMATE_END = 12.0
BACKUP_TRANSFER_START = 12.0
BACKUP_TRANSFER_END = 97.0
BACKUP_FINALIZE_START = 97.0

# Restore task.progress budget.
RESTORE_PREPARE_END = 5.0
RESTORE_ESTIMATE_END = 10.0
RESTORE_TRANSFER_START = 10.0
RESTORE_TRANSFER_END = 98.0
RESTORE_FINALIZE_START = 98.0

# Legacy aliases used by backup_orchestrator / backup_task imports.
_LOGIC_TASK_END = BACKUP_LOGIC_END
_KOPIA_TASK_START = BACKUP_PREPARE_END
_KOPIA_TASK_END = BACKUP_TRANSFER_END
_KOPIA_TASK_SPAN = BACKUP_TRANSFER_END - BACKUP_TRANSFER_START


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(max(0.0, min(100.0, value)), 2)))


def _float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _kopia_transfer_percent(kopia_payload: dict[str, Any]) -> float:
    aggregate = kopia_payload.get("aggregate") if isinstance(kopia_payload.get("aggregate"), dict) else {}
    if not aggregate.get("bytes_total_known"):
        parsed = _float(kopia_payload.get("display_percent"))
        if parsed is not None:
            return max(0.0, min(100.0, parsed))
        return 0.0
    for key in ("percent",):
        parsed = _float(aggregate.get(key))
        if parsed is not None:
            return max(0.0, min(100.0, parsed))
    parsed = _float(kopia_payload.get("display_percent"))
    if parsed is not None and kopia_payload.get("percent_source") == "kopia":
        return max(0.0, min(100.0, parsed))
    return 0.0


def _estimating_creep(
    *,
    started_at_raw: str | None,
    start_percent: float,
    end_percent: float,
    now: datetime | None = None,
) -> float:
    return progress_creep(
        started_at_raw=started_at_raw,
        start_percent=start_percent,
        end_percent=end_percent,
        now=now,
    )


def orchestrated_task_percent(
    *,
    task: Task,
    kopia_payload: dict[str, Any],
    kind: TaskKind,
    transfer: dict[str, Any] | None = None,
) -> float | None:
    phase = str(kopia_payload.get("orchestration_phase") or "").strip().lower()
    step = str(task.current_step or "").strip().lower()
    transfer = transfer if isinstance(transfer, dict) else {}

    if phase == "done" or str(task.status or "").lower() in {"success", "completed"}:
        return 100.0
    if phase == "failed" or str(task.status or "").lower() in {"failed", "timeout", "cancelled"}:
        return _float(task.progress)

    if kind == "backup":
        if step == "finalize_snapshot" or phase == "finalizing":
            return BACKUP_FINALIZE_START + 1.0
        if phase == "transferring":
            kopia_pct = _kopia_transfer_percent(kopia_payload)
            mapped = BACKUP_TRANSFER_START + (kopia_pct / 100.0) * (BACKUP_TRANSFER_END - BACKUP_TRANSFER_START)
            return max(_float(task.progress) or 0.0, mapped)
        if phase == "estimating":
            creep = _estimating_creep(
                started_at_raw=transfer.get("estimating_started_at"),
                start_percent=BACKUP_PREPARE_END,
                end_percent=BACKUP_ESTIMATE_END,
            )
            return max(_float(task.progress) or 0.0, creep)
        if phase in {"preparing", "dispatching", "stalled"}:
            return max(_float(task.progress) or 0.0, BACKUP_PREPARE_END)
        if step == "create_logic_snapshot":
            return max(_float(task.progress) or 0.0, min(BACKUP_LOGIC_END, 1.0))
        if step == "kopia_snapshot":
            return max(_float(task.progress) or 0.0, BACKUP_PREPARE_END)
        return _float(task.progress)

    if step == "finalize" or phase == "finalizing":
        return RESTORE_FINALIZE_START + 1.0
    if phase == "transferring":
        kopia_pct = _kopia_transfer_percent(kopia_payload)
        mapped = RESTORE_TRANSFER_START + (kopia_pct / 100.0) * (RESTORE_TRANSFER_END - RESTORE_TRANSFER_START)
        return max(_float(task.progress) or 0.0, mapped)
    if phase == "estimating":
        creep = _estimating_creep(
            started_at_raw=transfer.get("estimating_started_at"),
            start_percent=RESTORE_PREPARE_END,
            end_percent=RESTORE_ESTIMATE_END,
        )
        return max(_float(task.progress) or 0.0, creep)
    if phase in {"preparing", "dispatching"}:
        return max(_float(task.progress) or 0.0, RESTORE_PREPARE_END)
    return _float(task.progress)


def slim_transfer_progress(kopia_payload: dict[str, Any]) -> dict[str, Any]:
    aggregate = kopia_payload.get("aggregate") if isinstance(kopia_payload.get("aggregate"), dict) else {}
    label_args = kopia_payload.get("orchestration_label_args")
    return {
        "phase": str(kopia_payload.get("orchestration_phase") or "").strip().lower(),
        "label_key": str(kopia_payload.get("orchestration_label_key") or "").strip() or None,
        "label_args": label_args if isinstance(label_args, dict) else {},
        "progress_schema_version": int(aggregate.get("progress_schema_version") or 1),
        "bytes_done": int(aggregate.get("bytes_done") or 0),
        "processed_bytes": int(aggregate.get("processed_bytes") or aggregate.get("bytes_done") or 0),
        "bytes_total": aggregate.get("bytes_total"),
        "bytes_total_known": bool(aggregate.get("bytes_total_known")),
        "bytes_total_reference": bool(aggregate.get("bytes_total_reference")),
        "uploaded_bytes": int(aggregate.get("uploaded_bytes") or 0),
        "uploaded_count": int(aggregate.get("uploaded_count") or 0),
        "hashed_count": int(aggregate.get("hashed_count") or 0),
        "estimated_bytes": int(aggregate.get("estimated_bytes") or 0),
        "processed_count": int(aggregate.get("processed_count") or 0),
        "total_count": int(aggregate.get("total_count") or 0),
        "path_index": aggregate.get("path_index"),
        "path_total": aggregate.get("path_total"),
        "speed_bps": aggregate.get("speed_bps"),
        "processing_speed_bps": aggregate.get("processing_speed_bps"),
        "hash_speed_bps": aggregate.get("hash_speed_bps"),
        "upload_speed_bps": aggregate.get("upload_speed_bps"),
        "speed_source": aggregate.get("speed_source"),
        "processing_speed_source": aggregate.get("processing_speed_source"),
        "upload_speed_source": aggregate.get("upload_speed_source"),
        "display_percent": kopia_payload.get("display_percent"),
        "eta_seconds": aggregate.get("eta_seconds"),
        "eta_source": aggregate.get("eta_source"),
        "show_metrics": bool(kopia_payload.get("show_metrics")),
        "lanes_done": int(aggregate.get("lanes_done") or 0),
        "lanes_total": int(aggregate.get("lanes_total") or 0),
    }


def merge_transfer_progress(
    *,
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(current)
    prev = previous if isinstance(previous, dict) else {}
    phase = str(merged.get("phase") or "").lower()
    prev_phase = str(prev.get("phase") or "").lower()
    if phase == "estimating":
        if prev_phase == "estimating" and prev.get("estimating_started_at"):
            merged["estimating_started_at"] = prev["estimating_started_at"]
        elif not merged.get("estimating_started_at"):
            merged["estimating_started_at"] = timezone.now().isoformat()
    if phase == "transferring" and not merged.get("bytes_total_known"):
        if (
            prev_phase == "transferring"
            and prev.get("unknown_total_started_at")
            and not prev.get("bytes_total_known")
        ):
            merged["unknown_total_started_at"] = prev["unknown_total_started_at"]
        elif not merged.get("unknown_total_started_at"):
            merged["unknown_total_started_at"] = timezone.now().isoformat()
    elif merged.get("unknown_total_started_at"):
        merged.pop("unknown_total_started_at", None)

    if phase == "transferring" or prev_phase == "transferring":
        _merge_monotonic_transfer_metrics(merged=merged, previous=prev)
    return merged


def _merge_monotonic_transfer_metrics(*, merged: dict[str, Any], previous: dict[str, Any]) -> None:
    prev_done = max(0, int(previous.get("bytes_done") or 0))
    curr_done = max(0, int(merged.get("bytes_done") or 0))
    if prev_done > curr_done:
        merged["bytes_done"] = prev_done

    if previous.get("bytes_total_known") and not merged.get("bytes_total_known"):
        merged["bytes_total_known"] = True
        merged["bytes_total"] = previous.get("bytes_total")
        merged["bytes_total_reference"] = bool(previous.get("bytes_total_reference"))
    elif merged.get("bytes_total_known") and previous.get("bytes_total_known"):
        prev_total = int(previous.get("bytes_total") or 0)
        curr_total = int(merged.get("bytes_total") or 0)
        if prev_total > curr_total:
            merged["bytes_total"] = prev_total

    if merged.get("bytes_total_known"):
        total = int(merged.get("bytes_total") or 0)
        done = int(merged.get("bytes_done") or 0)
        if total > 0:
            computed = min(100.0, max(0.0, 100.0 * done / total))
            prev_display = _float(merged.get("display_percent"))
            prev_stored = _float(previous.get("display_percent"))
            merged["display_percent"] = max(
                value for value in (computed, prev_display, prev_stored) if value is not None
            )
        elif _float(previous.get("display_percent")) is not None:
            merged["display_percent"] = max(
                value for value in (_float(merged.get("display_percent")), _float(previous.get("display_percent")))
                if value is not None
            )

    if previous.get("show_metrics") and not merged.get("show_metrics"):
        merged["show_metrics"] = True


def persist_task_progress(
    *,
    task: Task,
    kopia_payload: dict[str, Any],
    kind: TaskKind,
    restore_record=None,
) -> dict[str, Any]:
    result_payload = dict(task.result_payload) if isinstance(task.result_payload, dict) else {}
    previous_transfer = result_payload.get("transfer_progress") if isinstance(result_payload.get("transfer_progress"), dict) else {}
    prebuilt = kopia_payload.get("transfer_progress") if isinstance(kopia_payload.get("transfer_progress"), dict) else {}
    current_slim = prebuilt if prebuilt.get("step3_display_percent") is not None else slim_transfer_progress(kopia_payload)
    transfer = merge_transfer_progress(
        previous=previous_transfer,
        current=current_slim,
    )
    kopia_payload = enrich_kopia_progress_payload(kopia_payload, transfer_progress=transfer)
    if prebuilt.get("step3_display_percent") is None:
        transfer = merge_transfer_progress(
            previous=previous_transfer,
            current=slim_transfer_progress(kopia_payload),
        )
        aggregate = kopia_payload.get("aggregate") if isinstance(kopia_payload.get("aggregate"), dict) else {}
        if kind == "backup":
            from apps.protection.services.progress.step3_progress import du_total_for_task, enrich_step3_backup_transfer

            transfer = enrich_step3_backup_transfer(
                transfer=transfer,
                previous=previous_transfer,
                aggregate=aggregate,
                du_total=du_total_for_task(task=task),
            )
        elif kind == "restore" and restore_record is not None:
            from apps.protection.services.progress.step3_progress import (
                enrich_step3_restore_transfer,
                restore_file_count_seed,
                restore_snapshot_bytes_total,
            )

            transfer = enrich_step3_restore_transfer(
                transfer=transfer,
                previous=previous_transfer,
                aggregate=aggregate,
                bytes_total=restore_snapshot_bytes_total(restore_record),
                file_count_seed=restore_file_count_seed(restore_record),
            )
    percent = orchestrated_task_percent(task=task, kopia_payload=kopia_payload, kind=kind, transfer=transfer)

    update_fields = ["updated_at", "result_payload"]
    result_payload["transfer_progress"] = transfer
    task.result_payload = result_payload

    if percent is not None:
        current = _float(task.progress) or 0.0
        phase = str(kopia_payload.get("orchestration_phase") or "").lower()
        if str(task.status or "").lower() in {Task.Status.PENDING, Task.Status.RUNNING}:
            if phase == "transferring":
                next_progress = percent
            else:
                next_progress = max(current, percent)
            if next_progress != current and (
                abs(next_progress - current) >= 0.01 or next_progress >= 99.99
            ):
                task.progress = _decimal(next_progress)
                update_fields.append("progress")
        elif str(kopia_payload.get("orchestration_phase") or "").lower() == "done" and current < 100:
            task.progress = _decimal(100.0)
            update_fields.append("progress")

    task.save(update_fields=list(dict.fromkeys(update_fields)))

    kopia_payload["task_progress"] = float(task.progress or 0)
    kopia_payload["transfer_progress"] = transfer
    return kopia_payload


def orchestrated_backup_from_agent_progress(
    *,
    task: Task,
    progress: dict[str, Any],
) -> float | None:
    if not isinstance(progress, dict) or not progress:
        return None
    if str(progress.get("phase") or "").lower() == "orchestration" or progress.get("orchestration_phase"):
        kopia_payload = {
            "orchestration_phase": "preparing",
            "orchestration_label": str(progress.get("orchestration_label") or "").strip(),
            "aggregate": {},
            "percent_source": "placeholder",
            "show_metrics": False,
        }
    elif str(progress.get("phase") or "").lower() == "kopia_transfer" or progress.get("kopia_phase"):
        phase = str(progress.get("kopia_phase") or progress.get("phase") or "").lower()
        if phase in {"uploading", "hashing", "restoring", "running", "processing"}:
            orch_phase = "transferring"
        elif phase == "finalizing":
            orch_phase = "finalizing"
        elif phase in {"done", "snapshot_created"}:
            orch_phase = "done"
        else:
            orch_phase = "estimating"
        kopia_percent = progress.get("kopia_percent", progress.get("percent"))
        kopia_payload = {
            "orchestration_phase": orch_phase,
            "aggregate": {"percent": _float(kopia_percent) or 0.0},
            "percent_source": "kopia" if orch_phase == "transferring" else "placeholder",
            "show_metrics": orch_phase == "transferring",
        }
    else:
        return None

    result_payload = dict(task.result_payload) if isinstance(task.result_payload, dict) else {}
    transfer = merge_transfer_progress(
        previous=result_payload.get("transfer_progress") if isinstance(result_payload.get("transfer_progress"), dict) else None,
        current=slim_transfer_progress(kopia_payload),
    )
    percent = orchestrated_task_percent(task=task, kopia_payload=kopia_payload, kind="backup", transfer=transfer)
    if percent is None:
        return None
    current = _float(task.progress) or 0.0
    if percent <= current + 0.1 and abs(percent - current) < 0.5:
        return None
    return max(current, percent)
