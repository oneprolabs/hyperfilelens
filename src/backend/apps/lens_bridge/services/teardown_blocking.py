"""Bounded retry state for durable Chat and Knowledge Source cleanup."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

INTERVENTION_ATTEMPT_THRESHOLD = max(
    1,
    int(getattr(settings, "LENS_TEARDOWN_INTERVENTION_ATTEMPTS", 12)),
)
INTERVENTION_AGE_SECONDS = max(
    60,
    int(getattr(settings, "LENS_TEARDOWN_INTERVENTION_SECONDS", 6 * 3600)),
)


def intervention_required(state: dict[str, Any] | None) -> bool:
    blocking = (state or {}).get("blocking")
    return bool(
        isinstance(blocking, dict)
        and blocking.get("intervention_required") is True
    )


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
    """Record one stable blocking condition and decide operator intervention."""

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
    same_condition = previous.get("fingerprint") == fingerprint
    first_seen = (
        _aware_timestamp(previous.get("first_seen_at")) if same_condition else None
    ) or now
    attempts = (
        int(previous.get("consecutive_attempts") or 0) + 1
        if same_condition
        else 1
    )
    elapsed_seconds = max(0, int((now - first_seen).total_seconds()))
    requires_intervention = (
        attempts >= INTERVENTION_ATTEMPT_THRESHOLD
        and elapsed_seconds >= INTERVENTION_AGE_SECONDS
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
        "intervention_required": requires_intervention,
    }
    updated["blocking"] = blocking
    return updated, blocking
