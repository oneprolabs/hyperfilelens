"""Versioned presentation contract derived from durable task results, never events.

This projection leaves orchestration and stored domain payloads unchanged. Both
tenant and operator serializers use it, including for tasks predating the contract.
"""
from __future__ import annotations

import re

_SECRET = r"authorization|cookie|password|passwd|secret|token|api[_-]?key|access[_-]?key|oauth[_-]?code|captcha|ciphertext"
_KEY = re.compile(_SECRET, re.I)
_ASSIGNMENT = re.compile(
    rf"((?:{_SECRET})[\w-]*\s*[\"']?\s*[:=]\s*)([\"'][^\"']*[\"']|[^\s,;}}&]+)",
    re.I,
)
_QUERY_PARAMETER = re.compile(
    rf"([?&](?:{_SECRET})\s*=\s*)([^&#\s]+)",
    re.I,
)


def sanitize_task_detail(value, depth=0):
    """Sanitize every exposed field, including legacy payloads and event text."""
    if depth > 24:
        return "[Truncated]"
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _KEY.search(str(key)) else sanitize_task_detail(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_task_detail(item, depth + 1) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value, flags=re.I)
        value = re.sub(r"(\w+://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", value)
        value = _QUERY_PARAMETER.sub(r"\1[REDACTED]", value)
        return _ASSIGNMENT.sub(r"\1[REDACTED]", value)
    return value


def _record(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _reasons(value, code="TASK_FAILED"):
    return [
        {"code": str(item.get("code") or code), "detail": str(item.get("detail") or item.get("message") or item.get("code") or code)}
        if isinstance(item, dict) else {"code": code, "detail": str(item)}
        for item in _list(value) if item
    ]


def task_error_contract(task, resources=()):
    status = str(task.status)
    if status not in {"failed", "timeout", "success", "partial", "cancelled"}:
        return None
    result = _record(task.result_payload)
    failure = _record(result.get("failure_details"))
    skipped = _record(result.get("skipped_details"))
    summary = _record(result.get("backup_summary"))
    cleanup = _list(result.get("cleanup_failures"))
    warnings = _list(result.get("warnings"))
    retained = _list(result.get("retained_resources"))
    children = [
        item for key in ("repository_cleanup_tasks", "snapshot_cleanup_tasks")
        for item in _list(result.get(key))
        if isinstance(item, dict) and item.get("status") in {"failed", "timeout", "cancelled"}
    ]
    partial = status == "partial" or result.get("result") == "partial_success" or result.get("outcome") == "partial_success"
    has_warning = bool(partial or result.get("cleanup_complete") is False or cleanup or warnings or retained or children
                       or skipped.get("count") or result.get("skipped_item_count") or result.get("skipped_file_count")
                       or result.get("skipped_directory_count") or failure or summary.get("failed_directories"))
    if status == "success" and not has_warning:
        return None
    if status == "cancelled":
        outcome = "cancelled"
        severity = "warning"
    else:
        outcome = "partial" if partial else "warning" if status == "success" else "timeout" if status == "timeout" else "failed"
        severity = "warning" if outcome in {"partial", "warning"} else "error"
    code = str(task.error_code or failure.get("category") or "TASK_FAILED")
    reasons = _reasons(result.get("reasons"), code) + _reasons(cleanup) + _reasons(warnings)
    if task.error_message:
        reasons.append({"code": code, "detail": str(task.error_message)})
    if failure:
        reasons.append({"code": str(failure.get("category") or code), "detail": str(failure.get("category") or code), "count": failure.get("total_count", failure.get("count", 0))})
    suggestions = _reasons(
        result.get("suggestions") or result.get("resolutions"),
        "review_task",
    )
    if result.get("hint"):
        suggestions.append({"code": "review_task", "detail": str(result["hint"])})
    suggestions += _reasons(failure.get("remediation"), "backup_remediation")
    if retained or cleanup or result.get("cleanup_complete") is False:
        suggestions.append({"code": "review_cleanup", "detail": "Review retained resources and complete any remaining cleanup before retrying."})
    if not suggestions:
        suggestions.append({"code": "review_task", "detail": "Review the affected resources and task details before retrying from the original workflow."})
    entities = []
    for resource in resources:
        kind = str(resource.resource_type)
        entities.append({"id": resource.resource_id, "name": str(resource.resource_id), "type": "repository" if "repository" in kind else "node" if kind in {"host", "node"} else "source"})
    for key in ("sources", "cleanup_failures", "reasons"):
        for item in _list(result.get(key)):
            if isinstance(item, dict) and item.get("source_id"):
                entities.append({"id": str(item["source_id"]), "name": str(item.get("source_name") or item["source_id"]), "type": "source", "error": str(item.get("detail") or "")})
    for item in _list(failure.get("items")) + _list(skipped.get("items")) + _list(summary.get("failed_directories")):
        if isinstance(item, dict) and item.get("path"):
            entities.append({"id": str(item["path"]), "name": str(item["path"]), "type": "source", "error": str(item.get("error") or "")})
    for item in children:
        entities.append({"id": str(item.get("task_uuid") or ""), "name": str(item.get("task_uuid") or ""), "type": "child_task", "error": str(item.get("error_message") or item.get("error_code") or item["status"])})
    return sanitize_task_detail({
        "version": 1,
        "severity": severity,
        "outcome": outcome,
        "summary": {"failed": "Task failed.", "timeout": "Task timed out.", "partial": "Task partially completed.", "warning": "Task completed with warnings.", "cancelled": "Task cancelled."}[outcome],
        "reasons": reasons,
        "suggestions": suggestions,
        "failed_step": result.get("failed_step") or (task.current_step if severity == "error" else None),
        "entities": entities,
        "cleanup_complete": result.get("cleanup_complete"),
        "cleanup_failures": cleanup,
        "retained_resources": retained,
        "skipped_items": skipped or {"count": result.get("skipped_item_count", 0)},
        "task_uuid": str(task.task_uuid),
        "correlation_id": str(result.get("correlation_id") or task.task_uuid),
        "error_code": task.error_code or failure.get("category"),
        "limited": not any((failure, skipped, cleanup, warnings, retained, children, result.get("reasons"), summary)),
        "technical_detail": result,
    })
