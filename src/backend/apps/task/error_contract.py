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
        {
            "code": str(item.get("code") or code),
            "detail": str(item.get("detail") or item.get("message") or item.get("code") or code),
            **({"snapshot_id": item["snapshot_id"]} if item.get("snapshot_id") else {}),
            **({
                "consumers": [
                    {"type": str(consumer.get("type") or ""),
                     "status": str(consumer.get("status") or "unknown")}
                    for consumer in item["consumers"]
                    if isinstance(consumer, dict)
                ],
            } if isinstance(item.get("consumers"), list) else {}),
        }
        if isinstance(item, dict) else {"code": code, "detail": str(item)}
        for item in _list(value) if item
    ]


def _repository_agent_unreachable(task) -> bool:
    return (
        str(getattr(task, "task_type", "") or "") == "repository_operation"
        and str(getattr(task, "error_code", "") or "") == "REPOSITORY_OPERATION_FAILED"
        and str(getattr(task, "error_message", "") or "").strip()
        == "agent websocket is not routable"
    )


def _repository_connection_role(task) -> str:
    operation = getattr(task, "repository_operation", None)
    if operation is None or operation.owner_type != "node" or not operation.owner_node_id:
        return "host"
    repository = operation.repository
    if (
        (
            repository.repo_type == "proxy_fs"
            or (repository.repo_type == "nas" and repository.bind_node_type == "proxy")
        )
        and repository.bind_node_id == operation.owner_node_id
    ):
        return "proxy"
    if (
        repository.repo_type == "nas"
        and not repository.bind_node_type
        and not repository.bind_node_id
    ):
        return "backup_host"
    return "host"


def _default_suggestion_code(task) -> str:
    task_type = str(getattr(task, "task_type", "") or "")
    error_code = str(getattr(task, "error_code", "") or "")
    if task_type == "source_unregister" and any(
        isinstance(reason, dict) and reason.get("code") == "snapshot_in_use"
        for reason in _list(_record(task.result_payload).get("reasons"))
    ):
        return "resolve_snapshot_usage"
    if (
        task_type == "repository_operation"
        and error_code == "CONTROL_PLANE_RESTART_INTERRUPTED"
    ):
        return "recover_controller_interruption"
    if task_type == "repository_operation" and error_code == "REPOSITORY_OPERATION_TIMEOUT":
        return "investigate_repository_timeout"
    if _repository_agent_unreachable(task):
        return f"reconnect_repository_{_repository_connection_role(task)}"
    return {
        "backup": "review_backup_diagnostics",
        "restore": "review_restore_diagnostics",
        "repository_operation": "review_repository_operation",
    }.get(task_type, "review_task")


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
        detail = str(task.error_message)
        if (
            str(task.task_type) == "repository_operation"
            and code == "REPOSITORY_OPERATION_TIMEOUT"
            and detail == (
                "Repository maintenance exceeded its execution time limit. Check "
                "the repository owner's activity and storage connectivity before retrying."
            )
        ):
            detail = "Repository maintenance did not finish within the execution time limit."
        reason_code = code
        if _repository_agent_unreachable(task):
            role = _repository_connection_role(task)
            reason_code = f"repository_{role}_unreachable"
            detail = {
                "proxy": "Unable to connect to the proxy host. The maintenance command could not be sent.",
                "backup_host": "Unable to connect to the backup host. The maintenance command could not be sent.",
                "host": "The maintenance command could not be sent because the host connection is unavailable.",
            }[role]
        reasons.append({"code": reason_code, "detail": detail})
    if failure:
        reasons.append({"code": str(failure.get("category") or code), "detail": str(failure.get("category") or code), "count": failure.get("total_count", failure.get("count", 0))})
    default_suggestion_code = _default_suggestion_code(task)
    suggestions = _reasons(
        result.get("suggestions") or result.get("resolutions"),
        default_suggestion_code,
    )
    if default_suggestion_code != "review_task":
        for suggestion in suggestions:
            if suggestion["code"] == "review_task":
                suggestion["code"] = default_suggestion_code
                suggestion["detail"] = default_suggestion_code
    if default_suggestion_code == "resolve_snapshot_usage":
        # Also repair the guidance for historical tasks whose generic hint
        # suggested Force Cleanup even though this request was already forced.
        suggestions = [{
            "code": "resolve_snapshot_usage",
            "detail": (
                "Wait for the restore or Chat preparation to finish. For a failed "
                "Chat, retry it until preparation succeeds, or delete it and wait "
                "for cleanup to complete, then submit a new source deregistration "
                "request. Force Cleanup cannot bypass snapshot usage protection."
            ),
        }]
    elif result.get("hint"):
        suggestions.append({"code": default_suggestion_code, "detail": str(result["hint"])})
    suggestions += _reasons(failure.get("remediation"), "backup_remediation")
    if retained or cleanup or result.get("cleanup_complete") is False:
        suggestions.append({"code": "review_cleanup", "detail": "Review retained resources and complete any remaining cleanup before retrying."})
    if not suggestions:
        suggestions.append({"code": default_suggestion_code, "detail": default_suggestion_code})
    entities = []
    for resource in resources:
        kind = str(resource.resource_type)
        resource_id = resource.resource_id
        if (
            str(task.task_type) == "source_unregister"
            and kind == "backup_source"
            and resource.resource_subtype in {"agent", "nas"}
        ):
            resource_id = f"{resource.resource_subtype}:{resource.resource_id}"
        entities.append({"id": resource_id, "name": str(resource.resource_id), "type": "repository" if "repository" in kind else "node" if kind in {"host", "node"} else "source"})
    for key in ("sources", "cleanup_failures", "reasons"):
        for item in _list(result.get(key)):
            if isinstance(item, dict) and item.get("source_id"):
                entities.append({"id": str(item["source_id"]), "name": str(item.get("source_name") or item["source_id"]), "type": "source", "error": str(item.get("detail") or "")})
    if str(task.task_type) == "source_unregister":
        merged = {}
        for entity in entities:
            key = (entity["type"], str(entity["id"]))
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(entity)
                continue
            if entity["name"] != str(entity["id"]):
                existing["name"] = entity["name"]
            if entity.get("error"):
                messages = existing.get("error", "").split("\n")
                if entity["error"] not in messages:
                    existing["error"] = "\n".join(
                        message for message in [*messages, entity["error"]] if message
                    )
        entities = list(merged.values())
    # Keep ignored/skipped source items out of the fatal entity list. They are
    # exposed through `skipped_items` below and rendered by the warning panel.
    for item in _list(failure.get("items")) + _list(summary.get("failed_directories")):
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


def node_task_error_contract(task, *, timed_out=False):
    """Project a terminal Agent task into the shared async-error contract.

    NAS mount/unmount commands are persisted as ``NodeTask`` rows rather than
    control-plane ``Task`` rows.  They still need the same stable, redacted
    presentation contract so callers do not have to scrape ``last_error``.
    """
    status = str(getattr(task, "status", "") or "").lower()
    if status not in {"failed", "timeout", "canceled", "cancelled", "success"} and not timed_out:
        return None
    result = _record(getattr(task, "result", None))
    kind = str(getattr(task, "kind", "") or "").lower()
    has_warning = bool(
        result.get("cleanup_complete") is False
        or result.get("warnings")
        or result.get("retained_resources")
    )
    if status == "success" and not has_warning:
        return None

    error_code = str(
        result.get("error_code")
        or (
            "AGENT.TIMEOUT"
            if timed_out or status == "timeout"
            else "AGENT.NAS_UNMOUNT_FAILED"
            if "unmount" in kind
            else "AGENT.NAS_MOUNT_FAILED"
            if kind.startswith("nas.")
            else "AGENT.TASK_FAILED"
        )
    )
    raw_error = str(getattr(task, "last_error", "") or "").strip()
    if timed_out or status in {"timeout"}:
        outcome = "timeout"
        severity = "error"
    elif status in {"canceled", "cancelled"}:
        outcome = "cancelled"
        severity = "warning"
    elif has_warning:
        outcome = "partial" if result.get("cleanup_complete") is False else "warning"
        severity = "warning"
    else:
        outcome = "failed"
        severity = "error"

    operation = (
        "unmount"
        if "unmount" in kind
        else "mount"
        if "mount" in kind
        else "connection test"
        if kind.endswith(".test")
        else "Agent operation"
    )
    summary = (
        f"NAS {operation} timed out."
        if outcome == "timeout"
        else f"NAS {operation} completed with warnings."
        if outcome in {"partial", "warning"}
        else f"NAS {operation} was cancelled."
        if outcome == "cancelled"
        else f"NAS {operation} failed."
    )
    reasons = _reasons(result.get("reasons"), error_code)
    if raw_error:
        reasons.append({"code": error_code, "detail": raw_error})
    if not reasons:
        reasons.append({"code": error_code, "detail": summary})
    suggestions = _reasons(
        result.get("suggestions") or result.get("resolutions"),
        "review_mount",
    )
    if not suggestions:
        suggestions.append({
            "code": "review_mount",
            "detail": (
                "Check the NAS address, share permissions, and Agent connectivity, "
                "then retry the originating workflow."
            ),
        })
    entities = []
    payload = _record(getattr(task, "payload", None))
    source_id = payload.get("source_resource_id") or payload.get("resource_id")
    if source_id:
        entities.append({
            "id": str(source_id),
            "name": str(payload.get("source_name") or source_id),
            "type": "source",
            "error": raw_error,
        })
    node_id = getattr(task, "node_id", None)
    if node_id:
        entities.append({"id": node_id, "name": str(node_id), "type": "node"})
    return sanitize_task_detail({
        "version": 1,
        "severity": severity,
        "outcome": outcome,
        "summary": summary,
        "reasons": reasons,
        "suggestions": suggestions,
        "failed_step": operation,
        "entities": entities,
        "cleanup_complete": result.get("cleanup_complete"),
        "cleanup_failures": _list(result.get("cleanup_failures")),
        "retained_resources": _list(result.get("retained_resources")),
        "task_uuid": str(task.id),
        "correlation_id": str(getattr(task, "correlation_id", "") or task.id),
        "error_code": error_code,
        "limited": not bool(result),
        "technical_detail": {
            "kind": getattr(task, "kind", ""),
            "status": status,
            "last_error": raw_error,
            "result": result,
        },
    })
