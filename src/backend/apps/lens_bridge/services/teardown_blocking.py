"""Bounded retry state for durable Chat and Knowledge Source cleanup."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

FORCED_REMOTE_CLEANUP_KEY = "forced_remote_cleanup"
RETRY_EXHAUSTED_KEY = "retry_exhausted"
MAX_AUTOMATIC_RETRY_ATTEMPTS = 3

# These reasons are safety fences, not ordinary cleanup failures. They must
# continue polling until the remote executor is known to have stopped. The
# old ``intervention_required`` marker is deliberately not part of this
# decision: it was also used for ordinary failures and made Chat deletion
# depend on an operator-only management command.
SAFETY_BLOCKING_REASONS = frozenset(
    {
        "canonical_restore_mismatch",
        "conversion_state_unknown",
        "conversion_stop_unconfirmed",
        "conversion_still_running",
        "restore_dispatch_still_stopping",
        "restore_executor_still_stopping",
        "restore_node_task_identity_mismatch",
        "restore_node_task_missing",
        "restore_task_missing",
        "workspace_binding_missing",
        "validate_gateway_workload",
    }
)


def forced_remote_cleanup(state: dict[str, Any] | None) -> dict[str, Any]:
    """Return the durable force-delete cleanup record, when present."""

    record = (state or {}).get(FORCED_REMOTE_CLEANUP_KEY)
    return dict(record) if isinstance(record, dict) else {}


def remote_cleanup_pending(state: dict[str, Any] | None) -> bool:
    """Return whether user-visible deletion left gateway-local cleanup due."""

    return forced_remote_cleanup(state).get("status") == "pending"


def intervention_required(state: dict[str, Any] | None) -> bool:
    """Legacy compatibility shim.

    Ordinary Chat cleanup must never require operator confirmation. Callers
    should use :func:`is_safety_blocking` or :func:`retry_exhausted` instead.
    """

    return False


def blocking_reason(state: dict[str, Any] | None) -> str:
    blocking = (state or {}).get("blocking")
    return str(blocking.get("reason") or "") if isinstance(blocking, dict) else ""


def is_safety_blocking(state: dict[str, Any] | None) -> bool:
    """Return whether the recorded failure is a remote-executor safety fence."""

    return blocking_reason(state) in SAFETY_BLOCKING_REASONS


def retry_exhausted(state: dict[str, Any] | None) -> bool:
    """Return whether automatic cleanup retries have been exhausted."""

    blocking = (state or {}).get("blocking")
    return bool(isinstance(blocking, dict) and blocking.get(RETRY_EXHAUSTED_KEY))


def clear_blocking(state: dict[str, Any] | None) -> dict[str, Any]:
    updated = dict(state or {})
    updated.pop("blocking", None)
    return updated


def _fingerprint(
    *,
    reason: str,
    task_id: str,
    gateway_link_id: int | None,
    remote_status: str,
) -> str:
    material = "\x00".join(
        [reason, task_id, str(gateway_link_id or ""), remote_status]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _aware_timestamp(value: Any) -> datetime | None:
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def record_blocking(
    state: dict[str, Any] | None,
    *,
    reason: str,
    task_id: str = "",
    gateway_link_id: int | None = None,
    remote_status: str = "",
    stop_confirmation_source: str = "",
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Record one stable blocking condition and decide automatic retry state."""

    now = now or timezone.now()
    updated = dict(state or {})
    previous = updated.get("blocking")
    previous = previous if isinstance(previous, dict) else {}
    fingerprint = _fingerprint(
        reason=reason,
        task_id=task_id,
        gateway_link_id=gateway_link_id,
        remote_status=remote_status,
    )
    # A legacy intervention marker must not carry its old retry budget into the
    # new model. The next attempt starts a fresh, user-retryable budget.
    same_condition = (
        previous.get("fingerprint") == fingerprint
        and RETRY_EXHAUSTED_KEY in previous
    )
    first_seen = (
        _aware_timestamp(previous.get("first_seen_at")) if same_condition else None
    ) or now
    attempts = (
        int(previous.get("consecutive_attempts") or 0) + 1
        if same_condition
        else 1
    )
    safety_blocking = reason in SAFETY_BLOCKING_REASONS
    retries_exhausted = (
        not safety_blocking and attempts >= MAX_AUTOMATIC_RETRY_ATTEMPTS
    )
    blocking = {
        "reason": reason,
        "fingerprint": fingerprint,
        "task_id": task_id,
        "gateway_link_id": gateway_link_id,
        "remote_status": remote_status,
        "stop_confirmation_source": stop_confirmation_source,
        "first_seen_at": first_seen.isoformat(),
        "last_seen_at": now.isoformat(),
        "consecutive_attempts": attempts,
        # Keep the field for old serializers and persisted rows, but it is no
        # longer used to require an operator action.
        "intervention_required": False,
        RETRY_EXHAUSTED_KEY: retries_exhausted,
    }
    updated["blocking"] = blocking
    return updated, blocking
