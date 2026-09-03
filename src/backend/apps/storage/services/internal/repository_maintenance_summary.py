from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any


MAINTENANCE_SUMMARY_SCHEMA_VERSION = 1

_GC_LINE = re.compile(
    r"^GC found (?P<count>\d+) (?P<kind>unused contents(?: that are too recent to delete)?|in-use contents|in-use system-contents) \((?P<size>[^)]+)\)$",
    re.IGNORECASE,
)
_GC_UNDELETED_LINE = re.compile(
    r"^GC undeleted (?P<count>\d+) contents \((?P<size>[^)]+)\)$",
    re.IGNORECASE,
)
_SIZE = re.compile(r"^(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTPE]?I?B)$", re.IGNORECASE)
_UNIT_FACTORS = {
    "B": 1,
    "KB": 1024,
    "KIB": 1024,
    "MB": 1024**2,
    "MIB": 1024**2,
    "GB": 1024**3,
    "GIB": 1024**3,
    "TB": 1024**4,
    "TIB": 1024**4,
    "PB": 1024**5,
    "PIB": 1024**5,
    "EB": 1024**6,
    "EIB": 1024**6,
}


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _current_successful_run(
    runs: object,
    *,
    started_at: datetime,
) -> dict[str, Any] | None:
    if not isinstance(runs, list):
        return None
    lower_bound = started_at - timedelta(seconds=2)
    matches: list[tuple[datetime, dict[str, Any]]] = []
    for raw in runs:
        if not isinstance(raw, dict) or raw.get("success") is not True:
            continue
        run_start = _parse_timestamp(raw.get("start"))
        if run_start is None or run_start < lower_bound:
            continue
        matches.append((run_start, raw))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _run_data(run: dict[str, Any] | None, kind: str) -> dict[str, Any] | None:
    if run is None:
        return None
    extra = run.get("extra")
    if not isinstance(extra, list):
        return None
    for item in extra:
        if not isinstance(item, dict) or item.get("kind") != kind:
            continue
        data = item.get("data")
        return data if isinstance(data, dict) else None
    return None


def _content_gc(data: dict[str, Any] | None) -> dict[str, int] | None:
    if data is None:
        return None
    return {
        "unused_count": _nonnegative_int(data.get("unreferencedContentCount")),
        "unused_bytes": _nonnegative_int(data.get("unreferencedContentSize")),
        "deleted_count": _nonnegative_int(data.get("deletedContentCount")),
        "deleted_bytes": _nonnegative_int(data.get("deletedContentSize")),
        "deferred_count": _nonnegative_int(data.get("unreferencedRecentContentCount")),
        "deferred_bytes": _nonnegative_int(data.get("unreferencedRecentContentSize")),
        "in_use_count": _nonnegative_int(data.get("inUseContentCount")),
        "in_use_bytes": _nonnegative_int(data.get("inUseContentSize")),
        "in_use_system_count": _nonnegative_int(data.get("inUseSystemContentCount")),
        "in_use_system_bytes": _nonnegative_int(data.get("inUseSystemContentSize")),
        "recovered_count": _nonnegative_int(data.get("recoveredContentCount")),
        "recovered_bytes": _nonnegative_int(data.get("recoveredContentSize")),
    }


def _pack_gc(data: dict[str, Any] | None) -> dict[str, int] | None:
    if data is None:
        return None
    return {
        "unreferenced_count": _nonnegative_int(data.get("unreferencedPackCount")),
        "unreferenced_bytes": _nonnegative_int(data.get("unreferencedTotalSize")),
        "deleted_count": _nonnegative_int(data.get("deletedPackCount")),
        "deleted_bytes": _nonnegative_int(data.get("deletedTotalSize")),
        "retained_count": _nonnegative_int(data.get("retainedPackCount")),
        "retained_bytes": _nonnegative_int(data.get("retainedTotalSize")),
    }


def parse_maintenance_info_summary(
    stdout: str,
    *,
    mode: str,
    started_at: datetime,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(stdout)
    except (TypeError, ValueError):
        return None
    schedule = payload.get("schedule") if isinstance(payload, dict) else None
    runs = schedule.get("runs") if isinstance(schedule, dict) else None
    if not isinstance(runs, dict):
        return None

    snapshot_run = _current_successful_run(runs.get("snapshot-gc"), started_at=started_at)
    pack_run = None
    for task_name in (
        "full-delete-blobs" if mode == "full" else "quick-delete-blobs",
        "full-delete-blobs",
        "quick-delete-blobs",
    ):
        pack_run = _current_successful_run(runs.get(task_name), started_at=started_at)
        if pack_run is not None:
            break

    content = _content_gc(_run_data(snapshot_run, "snapshotGCStats"))
    packs = _pack_gc(_run_data(pack_run, "deleteUnreferencedPacksStats"))
    if content is None and packs is None:
        return None
    return {
        "schema_version": MAINTENANCE_SUMMARY_SCHEMA_VERSION,
        "mode": mode,
        "source": "maintenance_info",
        "approximate": False,
        "content_gc": content,
        "pack_gc": packs,
    }


def _parse_size(value: str) -> int | None:
    match = _SIZE.fullmatch(value.strip())
    if match is None:
        return None
    factor = _UNIT_FACTORS.get(match.group("unit").upper())
    if factor is None:
        return None
    return max(0, int(float(match.group("value")) * factor))


def parse_maintenance_stderr_summary(stderr: str, *, mode: str) -> dict[str, Any] | None:
    content: dict[str, int] = {}
    for line in str(stderr or "").splitlines():
        text = line.strip()
        match = _GC_LINE.fullmatch(text)
        if match is not None:
            size = _parse_size(match.group("size"))
            if size is None:
                continue
            count = _nonnegative_int(match.group("count"))
            kind = match.group("kind").lower()
            if kind == "unused contents":
                content.update(
                    unused_count=count,
                    unused_bytes=size,
                    deleted_count=count,
                    deleted_bytes=size,
                )
            elif "too recent" in kind:
                content.update(deferred_count=count, deferred_bytes=size)
            elif kind == "in-use contents":
                content.update(in_use_count=count, in_use_bytes=size)
            else:
                content.update(in_use_system_count=count, in_use_system_bytes=size)
            continue
        undeleted = _GC_UNDELETED_LINE.fullmatch(text)
        if undeleted is not None:
            size = _parse_size(undeleted.group("size"))
            if size is not None:
                content.update(
                    recovered_count=_nonnegative_int(undeleted.group("count")),
                    recovered_bytes=size,
                )
    if not content:
        return None
    return {
        "schema_version": MAINTENANCE_SUMMARY_SCHEMA_VERSION,
        "mode": mode,
        "source": "stderr",
        "approximate": True,
        "content_gc": content,
        "pack_gc": None,
    }


def maintenance_summary_from_result(
    result: object,
    *,
    mode: str,
) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    existing = result.get("maintenance_summary")
    if isinstance(existing, dict) and existing.get("schema_version") == MAINTENANCE_SUMMARY_SCHEMA_VERSION:
        return existing
    maintenance = result.get("maintenance")
    if isinstance(maintenance, dict):
        nested = maintenance.get("maintenance_summary")
        if isinstance(nested, dict) and nested.get("schema_version") == MAINTENANCE_SUMMARY_SCHEMA_VERSION:
            return nested
        stderr = maintenance.get("stderr") or maintenance.get("stderr_tail")
    else:
        stderr = result.get("stderr") or result.get("stderr_tail")
    return parse_maintenance_stderr_summary(str(stderr or ""), mode=mode)
