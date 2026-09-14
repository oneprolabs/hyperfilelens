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


def _optional_nonnegative_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _selected_metrics(
    data: dict[str, Any] | None,
    fields: dict[str, str],
    boolean_fields: dict[str, str] | None = None,
) -> dict[str, int | bool] | None:
    if data is None:
        return None
    metrics: dict[str, int | bool] = {}
    for output_key, input_key in fields.items():
        value = _optional_nonnegative_int(data.get(input_key))
        if value is not None:
            metrics[output_key] = value
    for output_key, input_key in (boolean_fields or {}).items():
        value = data.get(input_key)
        if isinstance(value, bool):
            metrics[output_key] = value
    return metrics or None


def _maintenance_stage(
    stage_type: str,
    run: dict[str, Any] | None,
    *,
    statistics_kind: str,
    metric_fields: dict[str, str] | None,
    boolean_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    if run is None:
        return {
            "type": stage_type,
            "status": "not_run",
            "statistics_available": False,
            "metrics": None,
        }
    metrics = (
        _selected_metrics(
            _run_data(run, statistics_kind),
            metric_fields,
            boolean_fields,
        )
        if metric_fields is not None
        else None
    )
    return {
        "type": stage_type,
        "status": "completed",
        "statistics_available": metrics is not None,
        "metrics": metrics,
    }


def _quick_maintenance_stages(
    runs: dict[str, Any],
    *,
    started_at: datetime,
) -> list[dict[str, Any]] | None:
    epoch_compaction = _current_successful_run(
        runs.get("compact-single-epoch"),
        started_at=started_at,
    )
    epoch_advance = _current_successful_run(
        runs.get("advance-epoch"),
        started_at=started_at,
    )
    if epoch_compaction is not None or epoch_advance is not None:
        return [
            _maintenance_stage(
                "epoch_compaction",
                epoch_compaction,
                statistics_kind="compactSingleEpochStats",
                metric_fields={
                    "superseded_index_count": "supersededIndexBlobCount",
                    "superseded_index_bytes": "supersededIndexTotalSize",
                    "epoch": "epoch",
                },
            ),
            _maintenance_stage(
                "epoch_advance",
                epoch_advance,
                statistics_kind="advanceEpochStats",
                metric_fields={"current_epoch": "currentEpoch"},
                boolean_fields={"advanced": "wasAdvanced"},
            ),
        ]

    rewrite = _current_successful_run(
        runs.get("quick-rewrite-contents"),
        started_at=started_at,
    )
    pack = _current_successful_run(
        runs.get("quick-delete-blobs"),
        started_at=started_at,
    ) or _current_successful_run(
        runs.get("full-delete-blobs"),
        started_at=started_at,
    )
    index_compaction = _current_successful_run(
        runs.get("index-compaction"),
        started_at=started_at,
    )
    log_cleanup = _current_successful_run(
        runs.get("cleanup-logs"),
        started_at=started_at,
    )
    if all(run is None for run in (rewrite, pack, index_compaction, log_cleanup)):
        return None

    return [
        _maintenance_stage(
            "content_rewrite",
            rewrite,
            statistics_kind="rewriteContentsStats",
            metric_fields={
                "found_count": "toRewriteContentCount",
                "found_bytes": "toRewriteContentSize",
                "rewritten_count": "rewrittenContentCount",
                "rewritten_bytes": "rewrittenContentSize",
                "retained_count": "retainedContentCount",
                "retained_bytes": "retainedContentSize",
            },
        ),
        _maintenance_stage(
            "pack_gc",
            pack,
            statistics_kind="deleteUnreferencedPacksStats",
            metric_fields={
                "unreferenced_count": "unreferencedPackCount",
                "unreferenced_bytes": "unreferencedTotalSize",
                "deleted_count": "deletedPackCount",
                "deleted_bytes": "deletedTotalSize",
                "retained_count": "retainedPackCount",
                "retained_bytes": "retainedTotalSize",
            },
        ),
        _maintenance_stage(
            "index_compaction",
            index_compaction,
            statistics_kind="compactIndexesStats",
            metric_fields=None,
        ),
        _maintenance_stage(
            "log_cleanup",
            log_cleanup,
            statistics_kind="cleanupLogsStats",
            metric_fields={
                "candidate_count": "toDeleteBlobCount",
                "candidate_bytes": "toDeleteBlobSize",
                "deleted_count": "deletedBlobCount",
                "deleted_bytes": "deletedBlobSize",
                "retained_count": "retainedBlobCount",
                "retained_bytes": "retainedBlobSize",
            },
        ),
    ]


_CONTENT_GC_KEYS = frozenset(
    {
        "unused_count",
        "unused_bytes",
        "deleted_count",
        "deleted_bytes",
        "deferred_count",
        "deferred_bytes",
        "in_use_count",
        "in_use_bytes",
        "in_use_system_count",
        "in_use_system_bytes",
        "recovered_count",
        "recovered_bytes",
    }
)
_PACK_GC_KEYS = frozenset(
    {
        "unreferenced_count",
        "unreferenced_bytes",
        "deleted_count",
        "deleted_bytes",
        "retained_count",
        "retained_bytes",
    }
)
_STAGE_METRIC_KEYS = {
    "content_rewrite": frozenset(
        {
            "found_count",
            "found_bytes",
            "rewritten_count",
            "rewritten_bytes",
            "retained_count",
            "retained_bytes",
        }
    ),
    "pack_gc": _PACK_GC_KEYS,
    "index_compaction": frozenset(),
    "log_cleanup": frozenset(
        {
            "candidate_count",
            "candidate_bytes",
            "deleted_count",
            "deleted_bytes",
            "retained_count",
            "retained_bytes",
        }
    ),
    "epoch_compaction": frozenset(
        {"superseded_index_count", "superseded_index_bytes", "epoch"}
    ),
    "epoch_advance": frozenset({"current_epoch"}),
}


def _normalize_metric_group(
    value: object,
    allowed_keys: frozenset[str],
) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, int] = {}
    for key in allowed_keys:
        parsed = _optional_nonnegative_int(value.get(key))
        if parsed is not None:
            result[key] = parsed
    return result or None


def _normalize_stages(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        stage_type = item.get("type")
        status = item.get("status")
        if (
            not isinstance(stage_type, str)
            or stage_type not in _STAGE_METRIC_KEYS
            or stage_type in seen
            or status not in {"completed", "not_run"}
        ):
            continue
        seen.add(stage_type)
        metrics = (
            _normalize_metric_group(item.get("metrics"), _STAGE_METRIC_KEYS[stage_type])
            if status == "completed"
            else None
        )
        if (
            status == "completed"
            and stage_type == "epoch_advance"
            and isinstance(item.get("metrics"), dict)
            and isinstance(item["metrics"].get("advanced"), bool)
        ):
            metrics = dict(metrics or {})
            metrics["advanced"] = item["metrics"]["advanced"]
        result.append(
            {
                "type": stage_type,
                "status": status,
                "statistics_available": (
                    item.get("statistics_available") is True and metrics is not None
                ),
                "metrics": metrics,
            }
        )
    return result


def _normalize_existing_summary(
    value: object,
    *,
    mode: str,
) -> dict[str, Any] | None:
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != MAINTENANCE_SUMMARY_SCHEMA_VERSION
        or value.get("source") not in {"maintenance_info", "stderr"}
    ):
        return None
    content = _normalize_metric_group(value.get("content_gc"), _CONTENT_GC_KEYS)
    packs = _normalize_metric_group(value.get("pack_gc"), _PACK_GC_KEYS)
    stages = _normalize_stages(value.get("stages")) if mode == "quick" else []
    if content is None and packs is None and not stages:
        return None
    summary: dict[str, Any] = {
        "schema_version": MAINTENANCE_SUMMARY_SCHEMA_VERSION,
        "mode": mode,
        "source": value["source"],
        "approximate": value.get("approximate") is True,
        "content_gc": content,
        "pack_gc": packs,
    }
    if stages:
        summary["stages"] = stages
    return summary


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
    stages = (
        _quick_maintenance_stages(runs, started_at=started_at)
        if mode == "quick"
        else None
    )
    if content is None and packs is None and not stages:
        return None
    summary = {
        "schema_version": MAINTENANCE_SUMMARY_SCHEMA_VERSION,
        "mode": mode,
        "source": "maintenance_info",
        "approximate": False,
        "content_gc": content,
        "pack_gc": packs,
    }
    if stages:
        summary["stages"] = stages
    return summary


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
    existing = _normalize_existing_summary(result.get("maintenance_summary"), mode=mode)
    if existing is not None:
        return existing
    maintenance = result.get("maintenance")
    if isinstance(maintenance, dict):
        nested = _normalize_existing_summary(
            maintenance.get("maintenance_summary"),
            mode=mode,
        )
        if nested is not None:
            return nested
        stderr = maintenance.get("stderr") or maintenance.get("stderr_tail")
    else:
        stderr = result.get("stderr") or result.get("stderr_tail")
    return parse_maintenance_stderr_summary(str(stderr or ""), mode=mode)
