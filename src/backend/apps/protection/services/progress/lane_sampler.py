from __future__ import annotations

from datetime import datetime
from typing import Any

from django.utils import timezone

_MIN_SPEED_BPS = 100
_MIN_SAMPLE_GAP_SECONDS = 2.0
_METRIC_FRESHNESS_SECONDS = 6.0
_KOPIA_ETA_MIN_REMAINING_RATIO = 0.1
_KOPIA_ETA_MAX_COMPUTED_RATIO = 3.0


def _computed_eta_seconds(*, bytes_done: int, bytes_total: int, speed_bps: int) -> int | None:
    remaining = int(bytes_total) - int(bytes_done)
    if remaining <= 0 or speed_bps <= 0:
        return None
    return int(remaining / speed_bps)


def _kopia_eta_credible(*, kopia_eta: int, remaining: int, speed_bps: int) -> bool:
    if kopia_eta <= 0 or remaining <= 0 or speed_bps <= 0:
        return False
    implied_bytes = kopia_eta * speed_bps
    if implied_bytes < remaining * _KOPIA_ETA_MIN_REMAINING_RATIO:
        return False
    computed = remaining / speed_bps
    return kopia_eta <= computed * _KOPIA_ETA_MAX_COMPUTED_RATIO


def _lane_speed_counter(lane: dict[str, Any]) -> int:
    return int(lane.get("processed_bytes") or lane.get("bytes_done") or 0)


def _optional_int(mapping: dict[str, Any], key: str) -> int | None:
    if key not in mapping or mapping.get(key) in (None, ""):
        return None
    try:
        return max(0, int(mapping.get(key)))
    except (TypeError, ValueError):
        return None


def _parse_sampled_at(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
    except (TypeError, ValueError, OverflowError):
        return None


def apply_speed_and_eta(
    *,
    lane: dict[str, Any],
    sample: dict[str, Any] | None,
    now: datetime | None = None,
    persist_sample: bool = True,
) -> dict[str, Any]:
    now = now or timezone.now()
    result = dict(lane)
    schema_version = int(result.get("progress_schema_version") or 1)
    phase = str(result.get("kopia_phase") or "").lower()
    # Normalized backup lanes also carry a zero-valued processed_count field,
    # so lane type must come from the Kopia phase rather than key presence.
    is_restore = phase == "restoring"
    processing_counter = _lane_speed_counter(result)
    uploaded_counter = int(result.get("uploaded_bytes") or 0)
    bytes_done = int(result.get("bytes_done") or 0)
    bytes_total = result.get("bytes_total")
    bytes_total_known = bool(result.get("bytes_total_known"))

    processing_speed_bps = _optional_int(result, "processing_speed_bps")
    if processing_speed_bps is None:
        processing_speed_bps = _optional_int(result, "hash_speed_bps")
    upload_speed_bps = _optional_int(result, "upload_speed_bps")
    legacy_speed_bps = _optional_int(result, "speed_bps")
    processing_speed_source = result.get("processing_speed_source") or result.get("hash_speed_source")
    upload_speed_source = result.get("upload_speed_source")
    legacy_speed_source = result.get("speed_source")

    if schema_version < 2:
        if processing_speed_bps is None and phase == "hashing":
            processing_speed_bps = legacy_speed_bps
            processing_speed_source = legacy_speed_source
        if upload_speed_bps is None and phase in {"uploading", "restoring"}:
            upload_speed_bps = legacy_speed_bps
            upload_speed_source = legacy_speed_source

    prev = sample if isinstance(sample, dict) else {}
    prev_received_at = _parse_sampled_at(prev.get("sampled_at"))
    prev_counter_at = _parse_sampled_at(prev.get("counter_sampled_at")) or prev_received_at
    metrics_at = _parse_sampled_at(result.get("metrics_sampled_at"))
    freshness_at = prev_received_at or metrics_at
    metrics_fresh = (
        persist_sample
        or freshness_at is None
        or abs((now - freshness_at).total_seconds()) <= _METRIC_FRESHNESS_SECONDS
    )
    if not metrics_fresh:
        processing_speed_bps = None
        upload_speed_bps = None

    prev_processing = int(prev.get("processing_counter") or prev.get("counter") or prev.get("bytes_done") or 0)
    prev_uploaded = int(prev.get("uploaded_counter") or 0)
    counter_at = metrics_at or now
    delta_t = (counter_at - prev_counter_at).total_seconds() if prev_counter_at is not None else 0.0
    if metrics_fresh and prev_counter_at is not None and delta_t >= _MIN_SAMPLE_GAP_SECONDS:
        if processing_speed_bps is None and processing_counter >= prev_processing:
            processing_speed_bps = int((processing_counter - prev_processing) / delta_t)
            processing_speed_source = "delta"
        if upload_speed_bps is None and uploaded_counter >= prev_uploaded:
            upload_speed_bps = int((uploaded_counter - prev_uploaded) / delta_t)
            upload_speed_source = "delta"

    if is_restore and upload_speed_bps is None:
        upload_speed_bps = legacy_speed_bps
        upload_speed_source = legacy_speed_source

    result["processing_speed_bps"] = processing_speed_bps
    result["processing_speed_source"] = processing_speed_source if processing_speed_bps is not None else None
    result["hash_speed_bps"] = processing_speed_bps
    result["hash_speed_source"] = processing_speed_source if processing_speed_bps is not None else None
    result["upload_speed_bps"] = upload_speed_bps
    result["upload_speed_source"] = upload_speed_source if upload_speed_bps is not None else None
    result["speed_bps"] = upload_speed_bps
    result["speed_source"] = upload_speed_source if upload_speed_bps is not None else None

    kopia_eta = _optional_int(result, "kopia_eta_seconds") if metrics_fresh else None
    eta_speed_bps = upload_speed_bps if is_restore else processing_speed_bps
    if schema_version < 2 and eta_speed_bps is None:
        eta_speed_bps = upload_speed_bps or legacy_speed_bps
    computed_eta: int | None = None
    remaining = 0
    if bytes_total_known and bytes_total is not None and eta_speed_bps and eta_speed_bps >= _MIN_SPEED_BPS:
        remaining = int(bytes_total) - bytes_done
        computed_eta = _computed_eta_seconds(
            bytes_done=bytes_done,
            bytes_total=int(bytes_total),
            speed_bps=int(eta_speed_bps),
        )

    eta_seconds = None
    eta_source = None
    if phase not in {"finalizing", "done", "snapshot_created"}:
        if schema_version >= 2 and kopia_eta is not None:
            eta_seconds = kopia_eta
            eta_source = "kopia"
        elif kopia_eta is not None and (
            computed_eta is None
            or _kopia_eta_credible(
                kopia_eta=kopia_eta,
                remaining=remaining,
                speed_bps=int(eta_speed_bps or 0),
            )
        ):
            eta_seconds = kopia_eta
            eta_source = "kopia"
        elif computed_eta is not None:
            eta_seconds = computed_eta
            eta_source = "computed"

    result["eta_seconds"] = eta_seconds
    result["eta_source"] = eta_source

    max_total = (
        int(result.get("bytes_total") or 0)
        if bytes_total_known
        else int(prev.get("_max_bytes_total") or 0)
    )
    if bytes_total_known and bytes_total is not None:
        max_total = max(max_total, int(bytes_total))

    should_update_sample = not prev or delta_t >= _MIN_SAMPLE_GAP_SECONDS or (
        processing_counter < prev_processing or uploaded_counter < prev_uploaded
    )
    if persist_sample and should_update_sample:
        result["last_sample"] = {
            "bytes_done": bytes_done,
            "counter": processing_counter,
            "processing_counter": processing_counter,
            "uploaded_counter": uploaded_counter,
            "sampled_at": now.isoformat(),
            "counter_sampled_at": counter_at.isoformat(),
            "_max_bytes_total": max_total,
        }
    elif prev:
        result["last_sample"] = dict(prev)
    else:
        result["last_sample"] = {
            "bytes_done": bytes_done,
            "counter": processing_counter,
            "processing_counter": processing_counter,
            "uploaded_counter": uploaded_counter,
            "sampled_at": now.isoformat(),
            "counter_sampled_at": counter_at.isoformat(),
            "_max_bytes_total": max_total,
        }
    return result
