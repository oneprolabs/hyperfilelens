from __future__ import annotations

from typing import Any

_ACTIVE_STATUSES = frozenset({"pending", "dispatching", "running", "creating"})
_DONE_STATUSES = frozenset({"success", "available", "completed"})


def aggregate_lanes(lanes: list[dict[str, Any]]) -> dict[str, Any]:
    if not lanes:
        return {
            "percent": None,
            "progress_schema_version": 1,
            "bytes_done": 0,
            "processed_bytes": 0,
            "bytes_total": None,
            "bytes_total_known": False,
            "bytes_total_reference": False,
            "uploaded_bytes": 0,
            "uploaded_count": 0,
            "hashed_count": 0,
            "estimated_bytes": 0,
            "processed_count": 0,
            "total_count": 0,
            "path_index": None,
            "path_total": None,
            "speed_bps": None,
            "processing_speed_bps": None,
            "hash_speed_bps": None,
            "upload_speed_bps": None,
            "eta_seconds": None,
            "lanes_done": 0,
            "lanes_total": 0,
            "slowest_lane": None,
        }

    bytes_done = 0
    bytes_total = 0
    total_known = True
    reference_total = False
    uploaded_bytes = 0
    uploaded_count = 0
    hashed_count = 0
    estimated_bytes = 0
    processed_count = 0
    total_count = 0
    path_index: int | None = None
    path_total: int | None = None
    processing_speeds: list[int] = []
    upload_speeds: list[int] = []
    eta_candidates: list[tuple[dict[str, Any], int]] = []
    lanes_done = 0
    lanes_total = len(lanes)
    schema_version = 1

    for lane in lanes:
        status = str(lane.get("status") or "").lower()
        if status in _DONE_STATUSES:
            lanes_done += 1
        normalized = lane.get("progress") or {}
        if not isinstance(normalized, dict):
            normalized = {}
        schema_version = max(schema_version, int(normalized.get("progress_schema_version") or 1))
        done = int(normalized.get("bytes_done") or 0)
        total = normalized.get("bytes_total")
        total_known_lane = bool(normalized.get("bytes_total_known"))
        bytes_done += done
        uploaded_bytes += int(normalized.get("uploaded_bytes") or 0)
        uploaded_count += int(normalized.get("uploaded_count") or 0)
        hashed_count += int(normalized.get("hashed_count") or 0)
        lane_estimated = int(normalized.get("estimated_bytes") or 0)
        if total_known_lane and total is not None:
            lane_estimated = max(lane_estimated, int(total))
        estimated_bytes += lane_estimated
        lane_processed = int(normalized.get("processed_count") or 0)
        lane_total_count = int(normalized.get("total_count") or 0)
        if status in (_ACTIVE_STATUSES | _DONE_STATUSES) and normalized.get("is_transfer"):
            processed_count = max(processed_count, lane_processed)
            total_count = max(total_count, lane_total_count)
        if status in _ACTIVE_STATUSES and normalized.get("is_transfer"):
            lane_path_index = normalized.get("path_index")
            lane_path_total = normalized.get("path_total")
            if lane_path_index is not None:
                path_index = int(lane_path_index)
            if lane_path_total is not None:
                path_total = int(lane_path_total)

        if total_known_lane and total is not None:
            bytes_total += int(total)
            if normalized.get("bytes_total_reference"):
                reference_total = True
        else:
            total_known = False

        if status in _ACTIVE_STATUSES and normalized.get("is_transfer"):
            phase = str(normalized.get("kopia_phase") or "").lower()
            lane_processing = normalized.get("processing_speed_bps")
            if lane_processing is None:
                lane_processing = normalized.get("hash_speed_bps")
            lane_upload = normalized.get("upload_speed_bps")
            lane_speed = normalized.get("speed_bps")
            if lane_processing is not None:
                processing_speeds.append(int(lane_processing))
            elif lane_speed is not None and phase == "hashing":
                processing_speeds.append(int(lane_speed))
            if lane_upload is not None:
                upload_speeds.append(int(lane_upload))
            elif lane_speed is not None and phase in {"uploading", "restoring"}:
                upload_speeds.append(int(lane_speed))
            lane_eta = normalized.get("eta_seconds")
            if lane_eta is not None:
                eta_candidates.append((lane, int(lane_eta)))

    percent = None
    if total_known and bytes_total > 0:
        percent = min(100.0, max(0.0, 100.0 * bytes_done / bytes_total))
    if percent is None and total_known:
        lane_percents: list[float] = []
        for lane in lanes:
            normalized = lane.get("progress") or {}
            if not isinstance(normalized, dict):
                continue
            raw = normalized.get("percent")
            if raw is None:
                raw = normalized.get("kopia_percent")
            try:
                if raw not in (None, ""):
                    lane_percents.append(float(raw))
            except (TypeError, ValueError):
                continue
        if lane_percents:
            percent = min(100.0, max(0.0, max(lane_percents)))

    active_lanes = sum(
        1 for lane in lanes if str(lane.get("status") or "").lower() in _ACTIVE_STATUSES
    )
    if active_lanes > 0 and percent is not None and percent >= 100:
        percent = 99.0

    slowest_lane = None
    eta_seconds = None
    if eta_candidates:
        slowest_lane, eta_seconds = max(eta_candidates, key=lambda item: item[1])
        slowest_lane = {
            "id": slowest_lane.get("id"),
            "name": slowest_lane.get("name"),
            "eta_seconds": eta_seconds,
        }

    processing_speed_bps = sum(processing_speeds) if processing_speeds else None
    upload_speed_bps = sum(upload_speeds) if upload_speeds else None

    return {
        "percent": percent,
        "progress_schema_version": schema_version,
        "bytes_done": bytes_done,
        "processed_bytes": bytes_done,
        "bytes_total": bytes_total if total_known else None,
        "bytes_total_known": total_known,
        "bytes_total_reference": reference_total and total_known,
        "uploaded_bytes": uploaded_bytes,
        "uploaded_count": uploaded_count,
        "hashed_count": hashed_count,
        "estimated_bytes": estimated_bytes,
        "processed_count": processed_count,
        "total_count": total_count,
        "path_index": path_index,
        "path_total": path_total,
        "speed_bps": upload_speed_bps,
        "processing_speed_bps": processing_speed_bps,
        "hash_speed_bps": processing_speed_bps,
        "upload_speed_bps": upload_speed_bps,
        "eta_seconds": eta_seconds,
        "lanes_done": lanes_done,
        "lanes_total": lanes_total,
        "slowest_lane": slowest_lane,
    }
