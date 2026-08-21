from __future__ import annotations

from typing import Any

from apps.protection.services.progress.display import has_transfer_progress


def backup_orchestration_label_meta(
    *,
    task_status: str,
    lanes: list[dict[str, Any]],
    aggregate: dict[str, Any],
    current_step: str = "",
) -> tuple[dict[str, Any], str]:
    if task_status in {"success", "completed"}:
        return {"label_key": "protection.taskProgress.backup.done"}, "done"
    if task_status in {"failed", "timeout", "cancelled"}:
        return {"label_key": "protection.taskProgress.backup.failed"}, "failed"

    step = str(current_step or "").strip().lower()
    if step == "create_logic_snapshot":
        return {"label_key": "protection.taskProgress.backup.preparingLogic"}, "preparing"
    if step == "finalize_snapshot":
        return {"label_key": "protection.taskProgress.backup.finalizing"}, "finalizing"
    if step in {"dispatch_agent", "dispatch"}:
        return {"label_key": "protection.taskProgress.backup.dispatching"}, "dispatching"

    for lane in lanes:
        progress = lane.get("progress") or {}
        label = str(progress.get("orchestration_label") or "").strip()
        if label:
            return {"label_key": "protection.taskProgress.backup.preparing"}, "preparing"

    active_phases = {
        str((lane.get("progress") or {}).get("kopia_phase") or "").lower()
        for lane in lanes
        if str(lane.get("status") or "").lower() in {"running", "dispatching", "creating"}
    }
    if "finalizing" in active_phases:
        return {"label_key": "protection.taskProgress.backup.finalizing"}, "finalizing"

    stalled = [
        lane
        for lane in lanes
        if str(lane.get("status") or "").lower() == "running"
        and not (lane.get("progress") or {}).get("is_transfer")
    ]
    if stalled and has_transfer_progress(lanes):
        name = str(stalled[0].get("name") or "").strip() or "directory"
        return {
            "label_key": "protection.taskProgress.backup.stalled",
            "label_args": {"name": name},
        }, "stalled"

    active = any(
        str(lane.get("status") or "").lower() in {"running", "dispatching", "creating"}
        for lane in lanes
    )
    if active:
        if has_transfer_progress(lanes):
            uploaded_count = int(aggregate.get("uploaded_count") or 0)
            hashed_count = int(aggregate.get("hashed_count") or 0)
            if uploaded_count > 0:
                return {
                    "label_key": "protection.taskProgress.transfer.uploadedAndHashed",
                    "label_args": {
                        "uploaded": uploaded_count,
                        "hashed": hashed_count,
                    },
                }, "transferring"
            return {
                "label_key": "protection.taskProgress.transfer.hashedOnly",
                "label_args": {"hashed": hashed_count},
            }, "transferring"
        return {"label_key": "protection.taskProgress.backup.estimating"}, "estimating"
    return {"label_key": "protection.taskProgress.backup.preparing"}, "preparing"


def restore_orchestration_label_meta(
    *,
    task_status: str,
    lanes: list[dict[str, Any]],
    aggregate: dict[str, Any],
    current_step: str = "",
) -> tuple[dict[str, Any], str]:
    if task_status in {"success", "completed"}:
        return {"label_key": "protection.taskProgress.restore.done"}, "done"
    if task_status in {"failed", "timeout", "cancelled"}:
        return {"label_key": "protection.taskProgress.restore.failed"}, "failed"

    step = str(current_step or "").strip().lower()
    if step in {"dispatch_agent", "dispatch"}:
        return {"label_key": "protection.taskProgress.restore.dispatching"}, "dispatching"
    if step == "finalize":
        return {"label_key": "protection.taskProgress.restore.finalizing"}, "finalizing"

    for lane in lanes:
        progress = lane.get("progress") or {}
        label = str(progress.get("orchestration_label") or "").strip()
        if label:
            return {"label_key": "protection.taskProgress.restore.preparing"}, "preparing"

    active = any(str(lane.get("status") or "").lower() in {"running", "pending"} for lane in lanes)
    if active:
        if has_transfer_progress(lanes):
            processed = int(aggregate.get("processed_count") or 0)
            total_items = int(aggregate.get("total_count") or 0)
            path_index = aggregate.get("path_index")
            path_total = aggregate.get("path_total")
            label_args: dict[str, Any] = {
                "done": processed,
                "total": max(total_items, processed, 1) if total_items <= 0 else total_items,
                "i": int(path_index or 1),
                "m": int(path_total or 1),
            }
            return {
                "label_key": "protection.taskProgress.restore.itemsPath",
                "label_args": label_args,
            }, "transferring"
        return {"label_key": "protection.taskProgress.restore.estimating"}, "estimating"
    return {"label_key": "protection.taskProgress.restore.preparing"}, "preparing"


def _transfer_phase_key(lanes: list[dict[str, Any]]) -> str:
    phases = {
        str((lane.get("progress") or {}).get("kopia_phase") or "").lower()
        for lane in lanes
    }
    if "uploading" in phases:
        return "uploading"
    if "processing" in phases or "hashing" in phases:
        return "hashing"
    if "finalizing" in phases:
        return "finalizing"
    if "restoring" in phases:
        return "restoring"
    return "transferringGeneric"


def backup_orchestration_label(
    *,
    task_status: str,
    lanes: list[dict[str, Any]],
    aggregate: dict[str, Any],
    current_step: str = "",
) -> tuple[str, str]:
    meta, phase = backup_orchestration_label_meta(
        task_status=task_status,
        lanes=lanes,
        aggregate=aggregate,
        current_step=current_step,
    )
    _ = meta
    return "", phase


def restore_orchestration_label(
    *,
    task_status: str,
    lanes: list[dict[str, Any]],
    aggregate: dict[str, Any],
    current_step: str = "",
) -> tuple[str, str]:
    meta, phase = restore_orchestration_label_meta(
        task_status=task_status,
        lanes=lanes,
        aggregate=aggregate,
        current_step=current_step,
    )
    _ = meta
    return "", phase
