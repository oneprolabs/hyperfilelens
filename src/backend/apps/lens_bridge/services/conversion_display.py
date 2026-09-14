"""Present SourceLens conversion_summary for HFL product UI (Phase A).

SourceLens owns conversion execution and reason codes. HFL only formats them
for display and keeps format-matrix product expectations in one place.
"""

from __future__ import annotations

from typing import Any


# Product MVP expectations for Chat / Ask Your Data (not SL capability limits).
DOCUMENT_FORMAT_MATRIX: dict[str, list[str]] = {
    "recommended": [".pdf", ".docx"],
    "also_supported": [".pptx", ".xlsx"],
    "unsupported_mvp": [".doc"],
}

REASON_LABELS: dict[str, str] = {
    "PASSWORD_PROTECTED": "Password protected",
    "NO_EXTRACTABLE_TEXT": "No extractable text (may be scanned or empty)",
    "FILE_TOO_LARGE": "File exceeds size limit",
    "PAGE_LIMIT_EXCEEDED": "Page limit exceeded",
    "UNSUPPORTED_TYPE": "Unsupported file type",
    "UNCHANGED": "Already converted (unchanged)",
    "CONVERSION_FAILED": "Conversion failed",
    "CORRUPT": "File is corrupted or unreadable",
}

# Reasons that should appear in the user-facing problem list.
PROBLEM_REASONS: frozenset[str] = frozenset(
    {
        "PASSWORD_PROTECTED",
        "NO_EXTRACTABLE_TEXT",
        "FILE_TOO_LARGE",
        "PAGE_LIMIT_EXCEEDED",
        "UNSUPPORTED_TYPE",
        "CONVERSION_FAILED",
        "CORRUPT",
    }
)

# Reasons that are not failures (success / cache hit / empty).
OK_REASONS: frozenset[str] = frozenset({"", "UNCHANGED", "SUCCESS", "OK"})

WARNING_LABELS: dict[str, str] = {
    "CONVERSION_PARTIAL_FAILED": "Some documents could not be converted",
    "VISUAL_MODEL_NOT_CONFIGURED": (
        "Visual understanding is not configured; scanned PDFs may be unreadable"
    ),
}

_RUNNING_STATUSES: frozenset[str] = frozenset(
    {
        "STARTING",
        "PENDING",
        "STARTED",
        "PROGRESS",
        "RETRY",
        "RECEIVED",
        "RUNNING",
    }
)
_FAILED_STATUSES: frozenset[str] = frozenset({"FAILURE", "REVOKED"})


def reason_label(reason: str | None) -> str:
    code = str(reason or "").strip()
    if not code:
        return "Unknown reason"
    return REASON_LABELS.get(code, code.replace("_", " ").title())


def warning_label(code: str | None) -> str:
    raw = str(code or "").strip()
    if not raw:
        return ""
    return WARNING_LABELS.get(raw, raw.replace("_", " ").title())


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _conversion_phase(status: str) -> str:
    normalized = str(status or "").strip().upper()
    if normalized == "SUCCESS":
        return "succeeded"
    if normalized in _FAILED_STATUSES:
        return "failed"
    if normalized in _RUNNING_STATUSES or normalized:
        return "running"
    return "pending"


def _is_problem_reason(reason: str) -> bool:
    if reason in PROBLEM_REASONS:
        return True
    if not reason or reason in OK_REASONS:
        return False
    # Unknown non-OK reasons from newer SourceLens builds still surface.
    return True


def _item_view(row: dict[str, Any]) -> dict[str, Any]:
    reason = str(row.get("reason") or "").strip()
    name = str(row.get("name") or row.get("path") or row.get("source_path") or "").strip()
    path = str(row.get("path") or row.get("source_path") or "").strip()
    return {
        "name": name or path or "Unknown file",
        "path": path,
        "reason": reason,
        "reason_label": reason_label(reason) if reason else "",
        "is_problem": _is_problem_reason(reason),
    }


def document_conversion_view(conversion_state: Any) -> dict[str, Any] | None:
    """Shape ``sync_state_json.conversion`` for session/KS APIs.

    Returns ``None`` when no conversion state has been recorded yet.
    In-progress states are returned with ``phase=running`` and ``all_ok=False``
    so UIs do not treat an empty STARTING payload as success.
    """

    if not isinstance(conversion_state, dict) or not conversion_state:
        return None

    summary = conversion_state.get("summary")
    if not isinstance(summary, dict):
        summary = {}

    items_raw = [row for row in summary.get("items") or [] if isinstance(row, dict)]
    items = [_item_view(row) for row in items_raw]
    problem_items = [row for row in items if row.get("is_problem")]
    unchanged = sum(1 for row in items if row.get("reason") == "UNCHANGED")
    success = _int(summary.get("success"))
    failed = _int(summary.get("failed"))
    skipped = _int(summary.get("skipped"))
    unsupported = _int(summary.get("unsupported"))
    total = _int(summary.get("total"))
    candidates = _int(summary.get("candidates") or max(total - unsupported, 0))
    usable = success + unchanged > 0

    warnings_raw = [str(item) for item in conversion_state.get("warnings") or []]
    if not warnings_raw:
        warnings_raw = [str(item) for item in summary.get("warnings") or []]

    status = str(conversion_state.get("status") or "").strip()
    phase = _conversion_phase(status)
    progress_percent = conversion_state.get("progress_percent")
    try:
        progress_percent = (
            float(progress_percent) if progress_percent is not None else None
        )
    except (TypeError, ValueError):
        progress_percent = None

    error = str(conversion_state.get("error") or "").strip()
    # True only when at least one document is usable and nothing failed.
    # Zero-candidate runs are not "all ok" — UIs should show an empty/no-op state.
    all_ok = (
        phase == "succeeded"
        and usable
        and failed == 0
        and unsupported == 0
        and not problem_items
        and not error
    )
    empty_result = (
        phase == "succeeded"
        and not usable
        and failed == 0
        and unsupported == 0
        and not problem_items
        and not error
        and total == 0
        and candidates == 0
    )

    return {
        "status": status,
        "phase": phase,
        "all_ok": all_ok,
        "empty_result": empty_result,
        "progress_step": str(conversion_state.get("progress_step") or ""),
        "progress_message": str(conversion_state.get("progress_message") or ""),
        "progress_percent": progress_percent,
        "error": error,
        "finished_at": str(conversion_state.get("finished_at") or ""),
        "counts": {
            "total": total,
            "candidates": candidates,
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "unsupported": unsupported,
            "unchanged": unchanged,
        },
        "items": items,
        "problem_items": problem_items,
        "warnings": [
            {"code": code, "label": warning_label(code)}
            for code in warnings_raw
            if str(code).strip()
        ],
        "usable": usable,
        "format_matrix": DOCUMENT_FORMAT_MATRIX,
    }


def conversion_state_from_knowledge_source(ks: Any) -> Any:
    sync_state = getattr(ks, "sync_state_json", None)
    if not isinstance(sync_state, dict):
        return None
    return sync_state.get("conversion")


def data_context_for_session(
    *,
    backup_config_id: int | None,
    backup_source_snapshot_id: int | None,
    snapshot_created_at: Any,
    gateway_scope: str | None,
    gateway_name: str | None,
    gateway_selection_mode: str | None,
) -> dict[str, Any]:
    """Product semantics for where Chat data comes from (HFL-owned)."""

    scope = str(gateway_scope or "").strip().lower()
    mode = str(gateway_selection_mode or "").strip().lower()
    if scope in {"organization", "user", "private"} or mode == "manual":
        processing_location = "private_gateway"
    else:
        processing_location = "public_gateway"

    return {
        "origin": "protected_snapshot" if backup_source_snapshot_id else "unknown",
        "origin_label": (
            "Protected snapshot"
            if backup_source_snapshot_id
            else "Unknown data origin"
        ),
        "backup_config_id": backup_config_id,
        "backup_source_snapshot_id": backup_source_snapshot_id,
        "snapshot_created_at": (
            snapshot_created_at.isoformat()
            if hasattr(snapshot_created_at, "isoformat")
            else snapshot_created_at
        ),
        "processing_location": processing_location,
        "processing_location_label": (
            "Private Data Gateway"
            if processing_location == "private_gateway"
            else "Public Data Gateway"
        ),
        "gateway_name": gateway_name or "",
        "restore_path": (
            f"/protection/restore/snapshots/{backup_source_snapshot_id}"
            if backup_source_snapshot_id
            else ""
        ),
        "backup_detail_path": (
            f"/protection/backups/{backup_config_id}" if backup_config_id else ""
        ),
    }
