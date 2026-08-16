"""Per-user Copilot usage aggregation and durable HFL usage records."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
from typing import Any
import uuid

from django.db import transaction
from django.db.models import Count, F, Max, OrderBy, Q, Sum
from django.db.models.functions import TruncDate, TruncHour
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework.exceptions import NotFound

from apps.lens_bridge.models import LensSessionLink, LensSlUserLink, LensUsageLedger
from apps.lens_bridge.services import sl_client
from apps.protection.models import BackupConfig, BackupSourceSnapshot
from apps.protection.services.source_identity import resolve_source_display_name


logger = logging.getLogger(__name__)

TERMINAL_RUN_STATUSES = frozenset({"done", "failed", "cancelled"})
RECONCILIATION_INTERVAL_SECONDS = 30
RECONCILIATION_CLAIM_TTL_SECONDS = 300
RECONCILIATION_MAX_BACKOFF_SECONDS = 900
RECONCILIATION_NOT_FOUND_CONFIRMATIONS = 3
RECONCILIATION_NOT_FOUND_GRACE_SECONDS = 300


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if hasattr(value, "tzinfo"):
        return value
    parsed = parse_datetime(str(value))
    if parsed is not None and timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def _sl_user_link(user) -> LensSlUserLink | None:
    return LensSlUserLink.objects.filter(
        hfl_user=user,
        provision_status=LensSlUserLink.ProvisionStatus.READY,
        sl_user_id__gt=0,
    ).first()


def _session_context(link: LensSessionLink) -> dict[str, Any]:
    backup_source_name = ""
    if link.backup_config_id:
        config = BackupConfig.objects.filter(
            id=link.backup_config_id,
            organization_id=link.organization_id,
        ).first()
        if config is not None:
            backup_source_name = resolve_source_display_name(
                organization_id=link.organization_id,
                source_type=config.source_type,
                source_ref_id=config.source_ref_id,
                fallback=config.name,
            )
    snapshot_created_at = None
    if link.backup_source_snapshot_id:
        snapshot = BackupSourceSnapshot.objects.filter(
            id=link.backup_source_snapshot_id,
            organization_id=link.organization_id,
        ).first()
        if snapshot is not None:
            snapshot_created_at = (
                snapshot.finished_at or snapshot.started_at or snapshot.created_at
            )
    gateway_name = ""
    if link.gateway_selection_mode == LensSessionLink.GatewaySelectionMode.MANUAL:
        gateway_link = link.gateway_link
        if gateway_link is not None and gateway_link.gateway_id:
            gateway_name = gateway_link.gateway.name
    return {
        "session_link": link,
        "sl_session_uuid": link.sl_session_uuid,
        "chat_title": link.title,
        "backup_config_id": link.backup_config_id,
        "backup_source_name": backup_source_name,
        "backup_source_snapshot_id": link.backup_source_snapshot_id,
        "snapshot_created_at": snapshot_created_at,
        "source_scopes_json": list(link.source_scopes_json or []),
        "gateway_selection_mode": link.gateway_selection_mode,
        "gateway_name": gateway_name,
    }


def register_usage_run(
    link: LensSessionLink,
    *,
    run_uuid: uuid.UUID,
    question: str,
    status: str,
) -> LensUsageLedger | None:
    sl_user = _sl_user_link(link.hfl_user)
    if sl_user is None:
        return None
    registered_at = timezone.now()
    context = _session_context(link)
    defaults = {
        "organization": link.organization,
        "hfl_user": link.hfl_user,
        "sl_user_id": sl_user.sl_user_id,
        "question": question.strip(),
        "run_status": status or "queued",
        "occurred_at": registered_at,
        "reconciliation_next_at": registered_at,
        **context,
    }
    with transaction.atomic():
        row, created = LensUsageLedger.objects.get_or_create(
            sl_run_uuid=run_uuid,
            defaults=defaults,
        )
        if created:
            return row
        row = LensUsageLedger.objects.select_for_update().get(pk=row.pk)
        if row.organization_id != link.organization_id:
            raise ValueError("SourceLens run UUID belongs to another organization")
        row.hfl_user = link.hfl_user
        row.sl_user_id = sl_user.sl_user_id
        if not row.question and question.strip():
            row.question = question.strip()
        row.run_status = _stable_run_status(row.run_status, status)
        for field, value in context.items():
            if value not in (None, "", [], {}):
                setattr(row, field, value)
        if (
            row.run_status not in TERMINAL_RUN_STATUSES
            and row.reconciliation_next_at is None
        ):
            row.reconciliation_next_at = timezone.now()
        row.save()
        return row


def _stable_run_status(current: str, incoming: str) -> str:
    current_status = str(current or "").strip().lower()
    incoming_status = str(incoming or "").strip().lower()
    if (
        current_status in TERMINAL_RUN_STATUSES
        and incoming_status not in TERMINAL_RUN_STATUSES
    ):
        return current_status
    return incoming_status or current_status


def _publish_ai_token_usage(row: LensUsageLedger) -> None:
    status = str(row.run_status or "").strip().lower()
    if status not in TERMINAL_RUN_STATUSES:
        return
    occurred_at = row.finished_at or row.occurred_at
    measurement = {
        "status": status,
        "prompt_tokens": int(row.prompt_tokens or 0),
        "completion_tokens": int(row.completion_tokens or 0),
        "cached_tokens": int(row.cached_tokens or 0),
        "reasoning_tokens": int(row.reasoning_tokens or 0),
        "total_tokens": int(row.total_tokens or 0),
        "model_calls": int(row.model_calls or 0),
        "occurred_at": occurred_at.isoformat(),
    }
    fingerprint = hashlib.sha256(
        json.dumps(measurement, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]

    from apps.subscription.services.interface import record_usage_event

    record_usage_event(
        row.organization,
        idempotency_key=f"lens-run:{row.sl_run_uuid}:ai-tokens:{fingerprint}",
        meter_key="ai_tokens",
        quantity=measurement["total_tokens"],
        unit="token",
        source_type="lens_run",
        source_id=str(row.sl_run_uuid),
        occurred_at=occurred_at,
        event_kind="snapshot",
        attributes=measurement,
    )


def _run_call_details(
    run: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
        "model_calls": 0,
        "estimated_cost": None,
        "available": False,
    }
    total_cost = Decimal("0")
    has_missing_cost = False

    def add_call(payload: dict[str, Any]) -> None:
        nonlocal total_cost, has_missing_cost
        prompt = int(payload.get("prompt_tokens") or 0)
        completion = int(payload.get("completion_tokens") or 0)
        cached = int(payload.get("cached_tokens") or 0)
        reasoning = int(payload.get("reasoning_tokens") or 0)
        total = int(payload.get("total_tokens") or prompt + completion)
        cost = _decimal(payload.get("cost"))
        if cost is not None:
            total_cost += cost
        else:
            has_missing_cost = True
        calls.append(
            {
                "call": len(calls) + 1,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cached_tokens": cached,
                "reasoning_tokens": reasoning,
                "total_tokens": total,
                "estimated_cost": float(cost) if cost is not None else None,
            }
        )
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["cached_tokens"] += cached
        totals["reasoning_tokens"] += reasoning
        totals["total_tokens"] += total
        totals["model_calls"] += 1
        totals["available"] = True

    for step in run.get("steps") or []:
        detail = step.get("detail") if isinstance(step.get("detail"), dict) else step
        for event in detail.get("events") or []:
            if event.get("agent_event") == "llm.response":
                add_call(event)
        usage = detail.get("usage")
        if isinstance(usage, dict) and usage:
            add_call(usage)
    if calls:
        totals["estimated_cost"] = total_cost if not has_missing_cost else None
    else:
        summary_keys = {
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "reasoning_tokens",
            "total_tokens",
            "llm_calls",
            "model_calls",
            "total_cost",
        }
        if summary_keys.intersection(run):
            prompt = int(run.get("prompt_tokens") or 0)
            completion = int(run.get("completion_tokens") or 0)
            totals.update(
                {
                    "prompt_tokens": prompt,
                    "completion_tokens": completion,
                    "cached_tokens": int(run.get("cached_tokens") or 0),
                    "reasoning_tokens": int(run.get("reasoning_tokens") or 0),
                    "total_tokens": int(run.get("total_tokens") or prompt + completion),
                    "model_calls": int(
                        run.get("llm_calls") or run.get("model_calls") or 0
                    ),
                    "estimated_cost": _decimal(run.get("total_cost")),
                    "available": True,
                }
            )
    return calls, totals


@transaction.atomic
def capture_ledger_usage(
    row: LensUsageLedger,
    run: dict[str, Any],
    *,
    synced_at: datetime | None = None,
) -> LensUsageLedger:
    """Persist the latest SourceLens state in the authoritative HFL ledger."""

    row = LensUsageLedger.objects.select_for_update().get(pk=row.pk)
    calls, totals = _run_call_details(run)
    row.run_status = _stable_run_status(row.run_status, str(run.get("status") or ""))
    if not row.question:
        row.question = str(run.get("question") or "")
    if totals["available"]:
        incoming_total = int(totals["total_tokens"] or 0)
        prefer_incoming_details = incoming_total > int(row.total_tokens or 0) or (
            incoming_total == int(row.total_tokens or 0)
            and len(calls) >= int(row.model_calls or 0)
        )
        if prefer_incoming_details:
            # Treat one SourceLens response as an internally consistent
            # measurement. Taking a maximum per component can make token
            # subtotals exceed ``total_tokens`` after a corrected response.
            row.prompt_tokens = int(totals["prompt_tokens"] or 0)
            row.completion_tokens = int(totals["completion_tokens"] or 0)
            row.cached_tokens = int(totals["cached_tokens"] or 0)
            row.reasoning_tokens = int(totals["reasoning_tokens"] or 0)
            row.total_tokens = incoming_total
            row.model_calls = int(totals["model_calls"] or 0)
            row.estimated_cost = totals["estimated_cost"]
            row.call_details_json = calls
    if "error" in run:
        row.run_error = str(run.get("error") or "")
    started_at = _datetime(run.get("started_at"))
    if started_at is not None:
        row.started_at = min(
            value for value in (row.started_at, started_at) if value is not None
        )
    finished_at = _datetime(run.get("finished_at"))
    if finished_at is not None:
        row.finished_at = max(
            value for value in (row.finished_at, finished_at) if value is not None
        )
    row.occurred_at = row.started_at or row.occurred_at or timezone.now()
    row.source_synced_at = synced_at or timezone.now()
    row.reconciliation_attempts = 0
    row.reconciliation_claim_token = None
    row.reconciliation_claimed_at = None
    row.reconciliation_error = ""
    row.reconciliation_next_at = (
        None
        if row.run_status in TERMINAL_RUN_STATUSES
        else row.source_synced_at + timedelta(seconds=RECONCILIATION_INTERVAL_SECONDS)
    )
    row.save(
        update_fields=[
            "run_status",
            "question",
            "prompt_tokens",
            "completion_tokens",
            "cached_tokens",
            "reasoning_tokens",
            "total_tokens",
            "model_calls",
            "estimated_cost",
            "call_details_json",
            "run_error",
            "started_at",
            "finished_at",
            "occurred_at",
            "source_synced_at",
            "reconciliation_attempts",
            "reconciliation_claim_token",
            "reconciliation_claimed_at",
            "reconciliation_error",
            "reconciliation_next_at",
            "updated_at",
        ]
    )
    _publish_ai_token_usage(row)
    return row


def capture_run_usage(
    link: LensSessionLink, run: dict[str, Any]
) -> LensUsageLedger | None:
    """Capture SourceLens run usage discovered during the interactive flow."""

    raw_uuid = run.get("uuid") or link.active_run_uuid
    if not raw_uuid:
        return None
    run_uuid = uuid.UUID(str(raw_uuid))
    row = LensUsageLedger.objects.filter(sl_run_uuid=run_uuid).first()
    if row is None:
        row = register_usage_run(
            link,
            run_uuid=run_uuid,
            question="",
            status=str(run.get("status") or ""),
        )
    if row is None:
        return None
    return capture_ledger_usage(row, run)


def _public_run_failure(status: str, raw_error: str) -> tuple[str, str]:
    """Return a stable, tenant-safe error code and message for a terminal run."""
    normalized_status = str(status or "").strip().lower()
    normalized_error = str(raw_error or "").strip().upper()
    if normalized_status == "cancelled":
        return "RUN_CANCELLED", "The AI response was cancelled."
    if "TIMEOUT" in normalized_error:
        return (
            "MODEL_TIMEOUT",
            "The AI model timed out. Try again or choose another model.",
        )
    if any(
        marker in normalized_error
        for marker in ("MODEL", "PROVIDER", "QUOTA", "BALANCE", "PAYMENT")
    ):
        return (
            "MODEL_PROVIDER_ERROR",
            "The AI model provider rejected the request. Check model availability, quota, or account balance, then try again.",
        )
    return (
        "AI_RUN_FAILED",
        "The AI response failed. Try again or contact your administrator.",
    )


def run_outcomes_for_messages(
    link: LensSessionLink,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return durable, sanitized terminal outcomes for runs in this message page."""
    ordered_run_ids: list[uuid.UUID] = []
    seen = set()
    for message in messages:
        raw_run_id = message.get("run")
        if not raw_run_id:
            continue
        try:
            run_id = uuid.UUID(str(raw_run_id))
        except (TypeError, ValueError):
            continue
        if run_id in seen:
            continue
        seen.add(run_id)
        ordered_run_ids.append(run_id)
    if not ordered_run_ids:
        return []

    rows = {
        row.sl_run_uuid: row
        for row in LensUsageLedger.objects.filter(
            session_link=link,
            sl_run_uuid__in=ordered_run_ids,
            run_status__in={"failed", "cancelled"},
        )
    }
    outcomes = []
    for run_id in ordered_run_ids:
        row = rows.get(run_id)
        if row is None:
            continue
        error_code, message = _public_run_failure(row.run_status, row.run_error)
        outcomes.append(
            {
                "run_uuid": str(run_id),
                "status": row.run_status,
                "error_code": error_code,
                "message": message,
                "finished_at": row.finished_at,
            }
        )
    return outcomes


def seed_missing_active_run_ledgers(*, limit: int = 100) -> list[int]:
    """Create ledgers for active runs whose initial local write was interrupted."""

    bounded_limit = max(1, min(int(limit), 500))
    session_links = list(
        LensSessionLink.objects.filter(active_run_uuid__isnull=False)
        .exclude(usage_records__sl_run_uuid=F("active_run_uuid"))
        .select_related(
            "organization",
            "hfl_user",
            "gateway_link__gateway",
        )
        .order_by("updated_at", "id")[:bounded_limit]
    )
    seeded_ids: list[int] = []
    for link in session_links:
        row = register_usage_run(
            link,
            run_uuid=link.active_run_uuid,
            question="",
            status=link.active_run_status or "queued",
        )
        if row is not None:
            seeded_ids.append(row.id)
    return seeded_ids


def claim_due_usage_ledgers(
    *,
    limit: int,
    now: datetime,
) -> list[tuple[int, uuid.UUID]]:
    """Claim due ledger rows so overlapping workers cannot reconcile them."""

    stale_claim = now - timedelta(seconds=RECONCILIATION_CLAIM_TTL_SECONDS)
    bounded_limit = max(1, min(int(limit), 500))
    with transaction.atomic():
        rows = list(
            LensUsageLedger.objects.select_for_update(skip_locked=True)
            .filter(hfl_user__isnull=False)
            .filter(
                Q(source_synced_at__isnull=True)
                | ~Q(run_status__in=TERMINAL_RUN_STATUSES)
            )
            .filter(
                Q(reconciliation_next_at__isnull=True)
                | Q(reconciliation_next_at__lte=now)
            )
            .filter(
                Q(reconciliation_claimed_at__isnull=True)
                | Q(reconciliation_claimed_at__lte=stale_claim)
            )
            .order_by("reconciliation_next_at", "updated_at", "id")[:bounded_limit]
        )
        claims: list[tuple[int, uuid.UUID]] = []
        for row in rows:
            claim_token = uuid.uuid4()
            row.reconciliation_claim_token = claim_token
            row.reconciliation_claimed_at = now
            claims.append((row.id, claim_token))
        if rows:
            LensUsageLedger.objects.bulk_update(
                rows,
                ["reconciliation_claim_token", "reconciliation_claimed_at"],
            )
    return claims


def record_reconciliation_failure(
    *,
    ledger_id: int,
    claim_token: uuid.UUID,
    message: str,
    now: datetime,
) -> None:
    """Release a failed claim and persist bounded exponential backoff."""

    row = LensUsageLedger.objects.filter(
        id=ledger_id,
        reconciliation_claim_token=claim_token,
    ).first()
    if row is None:
        return
    attempts = row.reconciliation_attempts + 1
    delay_seconds = min(
        RECONCILIATION_INTERVAL_SECONDS * (2 ** min(attempts - 1, 8)),
        RECONCILIATION_MAX_BACKOFF_SECONDS,
    )
    LensUsageLedger.objects.filter(
        id=ledger_id,
        reconciliation_claim_token=claim_token,
    ).update(
        reconciliation_attempts=attempts,
        reconciliation_claim_token=None,
        reconciliation_claimed_at=None,
        reconciliation_next_at=now + timedelta(seconds=delay_seconds),
        reconciliation_error=str(message or "Usage reconciliation failed.")[:2000],
    )


def _finalize_missing_source_run(
    *,
    ledger_id: int,
    claim_token: uuid.UUID,
    now: datetime,
) -> str | None:
    """Close a ledger whose SourceLens run has been durably removed."""

    row = LensUsageLedger.objects.filter(
        id=ledger_id,
        reconciliation_claim_token=claim_token,
    ).first()
    if row is None:
        return None
    updates: dict[str, Any] = {
        "source_synced_at": now,
        "reconciliation_claim_token": None,
        "reconciliation_claimed_at": None,
        "reconciliation_next_at": None,
        "reconciliation_error": "SourceLens run no longer exists.",
        "updated_at": now,
    }
    if row.run_status not in TERMINAL_RUN_STATUSES:
        updates.update(
            {
                "run_status": "failed",
                "run_error": "SOURCE_RUN_NOT_FOUND",
                "finished_at": now,
            }
        )
    LensUsageLedger.objects.filter(
        id=ledger_id,
        reconciliation_claim_token=claim_token,
    ).update(**updates)
    LensSessionLink.objects.filter(
        id=row.session_link_id,
        active_run_uuid=row.sl_run_uuid,
    ).update(
        active_run_uuid=None,
        active_run_status="",
        updated_at=now,
    )
    return str(updates.get("run_status") or row.run_status)


def _missing_source_run_is_confirmed(
    row: LensUsageLedger,
    *,
    now: datetime,
) -> bool:
    """Return whether repeated 404s are safe to treat as durable removal."""

    observations = row.reconciliation_attempts + 1
    grace_deadline = row.created_at + timedelta(
        seconds=RECONCILIATION_NOT_FOUND_GRACE_SECONDS,
    )
    return (
        observations >= RECONCILIATION_NOT_FOUND_CONFIRMATIONS and now >= grace_deadline
    )


def reconcile_claimed_usage_ledger(
    *,
    ledger_id: int,
    claim_token: uuid.UUID,
) -> dict[str, Any]:
    """Synchronize one claimed SourceLens run into the HFL ledger."""

    row = (
        LensUsageLedger.objects.select_related(
            "hfl_user",
            "session_link",
        )
        .filter(
            id=ledger_id,
            reconciliation_claim_token=claim_token,
        )
        .first()
    )
    if row is None:
        return {"status": "skipped", "ledger_id": ledger_id}
    if row.hfl_user is None:
        message = "The HFL user no longer exists."
        record_reconciliation_failure(
            ledger_id=ledger_id,
            claim_token=claim_token,
            message=message,
            now=timezone.now(),
        )
        return {"status": "failed", "ledger_id": ledger_id, "error": message}
    try:
        run = sl_client.request_json(
            "GET",
            f"/api/lens/runs/{row.sl_run_uuid}/",
            hfl_user=row.hfl_user,
            timeout=30,
        )
        if not isinstance(run, dict):
            raise ValueError("SourceLens returned an invalid run payload.")
        returned_uuid = run.get("uuid")
        if returned_uuid and uuid.UUID(str(returned_uuid)) != row.sl_run_uuid:
            raise ValueError("SourceLens returned a different run.")
    except sl_client.LensBridgeError as exc:
        missing_status = None
        failure_time = timezone.now()
        if exc.status_code == 404 and _missing_source_run_is_confirmed(
            row, now=failure_time
        ):
            missing_status = _finalize_missing_source_run(
                ledger_id=ledger_id,
                claim_token=claim_token,
                now=failure_time,
            )
        if missing_status is not None:
            return {
                "status": missing_status,
                "ledger_id": ledger_id,
                "terminal": True,
                "source_missing": True,
            }
        logger.warning(
            "usage reconciliation failed ledger_id=%s: %s",
            ledger_id,
            exc,
        )
        record_reconciliation_failure(
            ledger_id=ledger_id,
            claim_token=claim_token,
            message=str(exc),
            now=failure_time,
        )
        return {
            "status": "failed",
            "ledger_id": ledger_id,
            "error": str(exc)[:1000],
        }
    except (TypeError, ValueError) as exc:
        logger.warning(
            "usage reconciliation failed ledger_id=%s: %s",
            ledger_id,
            exc,
        )
        record_reconciliation_failure(
            ledger_id=ledger_id,
            claim_token=claim_token,
            message=str(exc),
            now=timezone.now(),
        )
        return {
            "status": "failed",
            "ledger_id": ledger_id,
            "error": str(exc)[:1000],
        }

    row = LensUsageLedger.objects.filter(
        id=ledger_id,
        reconciliation_claim_token=claim_token,
    ).first()
    if row is None:
        return {"status": "skipped", "ledger_id": ledger_id}
    row = capture_ledger_usage(row, run)
    if row.run_status in TERMINAL_RUN_STATUSES:
        LensSessionLink.objects.filter(
            id=row.session_link_id,
            active_run_uuid=row.sl_run_uuid,
        ).update(
            active_run_uuid=None,
            active_run_status="",
            updated_at=timezone.now(),
        )
    return {
        "status": row.run_status,
        "ledger_id": ledger_id,
        "terminal": row.run_status in TERMINAL_RUN_STATUSES,
    }


def reconcile_usage_ledgers(*, limit: int = 100) -> dict[str, Any]:
    """Synchronize a bounded batch; intended for tests and repair commands."""

    claims = claim_due_usage_ledgers(limit=limit, now=timezone.now())
    results = [
        reconcile_claimed_usage_ledger(
            ledger_id=ledger_id,
            claim_token=claim_token,
        )
        for ledger_id, claim_token in claims
    ]
    return {
        "claimed": len(claims),
        "reconciled": sum(
            row["status"] not in {"failed", "skipped"} for row in results
        ),
        "pending": sum(
            row["status"] not in TERMINAL_RUN_STATUSES | {"failed", "skipped"}
            for row in results
        ),
        "failed": [row for row in results if row["status"] == "failed"],
    }


def _date_range(params) -> tuple[str, str]:
    today = timezone.localdate()
    default_start = today
    raw_start = str(params.get("start_date") or "")
    raw_end = str(params.get("end_date") or "")
    start = parse_date(raw_start) or default_start
    end = parse_date(raw_end) or today
    if start > end:
        start, end = end, start
    return start.isoformat(), end.isoformat()


def _scope_summary(scopes: list[dict[str, Any]]) -> str:
    if not scopes:
        return "No Content"
    types = [str(item.get("path_type") or "unknown") for item in scopes]
    if all(value == "file" for value in types):
        label = "File"
    elif all(value == "dir" for value in types):
        label = "Folder"
    else:
        label = "Item"
    return f"{len(scopes)} {label}{'' if len(scopes) == 1 else 's'}"


def _ledger_item(row: LensUsageLedger) -> dict[str, Any]:
    return {
        "run_uuid": str(row.sl_run_uuid),
        "time": row.occurred_at,
        "chat_id": row.session_link_id,
        "chat_title": row.chat_title or "Deleted Chat",
        "chat_available": bool(
            row.session_link_id
            and row.session_link
            and row.session_link.lifecycle_status
            != LensSessionLink.LifecycleStatus.DELETED
        ),
        "backup_source_name": row.backup_source_name or "Backup Source",
        "scope_summary": _scope_summary(list(row.source_scopes_json or [])),
        "question": row.question,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "cached_tokens": row.cached_tokens,
        "reasoning_tokens": row.reasoning_tokens,
        "total_tokens": row.total_tokens,
        "model_calls": row.model_calls,
        "estimated_cost": float(row.estimated_cost)
        if row.estimated_cost is not None
        else None,
        "cost_currency": row.cost_currency,
        "status": row.run_status,
    }


def _trend_item(bucket: str, row: dict[str, Any]) -> dict[str, Any]:
    request_count = int(row.get("request_count") or 0)
    costed_requests = int(row.get("costed_requests") or 0)
    if request_count == 0:
        total_cost: float | None = 0
    elif costed_requests == request_count and row.get("total_cost") is not None:
        total_cost = float(row["total_cost"])
    else:
        total_cost = None
    return {
        "bucket": bucket,
        "total_calls": row.get("total_calls") or 0,
        "total_prompt_tokens": row.get("total_prompt_tokens") or 0,
        "total_completion_tokens": row.get("total_completion_tokens") or 0,
        "total_cached_tokens": row.get("total_cached_tokens") or 0,
        "total_reasoning_tokens": row.get("total_reasoning_tokens") or 0,
        "total_tokens": row.get("total_tokens") or 0,
        "total_cost": total_cost,
    }


def usage_overview(org, user, params) -> dict[str, Any]:
    start_date, end_date = _date_range(params)
    sl_user = _sl_user_link(user)
    if sl_user is None:
        return {
            "period": {"start_date": start_date, "end_date": end_date},
            "summary": {
                "estimated_cost": 0,
                "cost_currency": "USD",
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_tokens": 0,
                "reasoning_tokens": 0,
                "model_calls": 0,
                "q_and_a_requests": 0,
                "average_cost_per_q_and_a": 0,
            },
            "trend": [],
            "by_backup_source": [],
            "backup_sources": [],
            "results": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
            "data_freshness": {
                "last_source_sync_at": None,
                "pending_runs": 0,
            },
        }

    start = parse_date(start_date)
    end = parse_date(end_date)
    queryset = LensUsageLedger.objects.filter(
        organization=org,
        hfl_user=user,
        occurred_at__date__gte=start,
        occurred_at__date__lte=end,
    ).select_related("session_link")
    query = str(params.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(question__icontains=query)
            | Q(chat_title__icontains=query)
            | Q(backup_source_name__icontains=query)
        )
    backup_source = str(params.get("backup_source") or "").strip()
    if backup_source:
        queryset = queryset.filter(backup_source_name=backup_source)
    run_status = str(params.get("status") or "").strip()
    if run_status:
        queryset = queryset.filter(run_status=run_status)

    try:
        page = max(1, int(params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(100, max(1, int(params.get("page_size") or 20)))
    except (TypeError, ValueError):
        page_size = 20
    total = queryset.count()
    offset = (page - 1) * page_size
    results = [_ledger_item(row) for row in queryset[offset : offset + page_size]]

    period_rows = LensUsageLedger.objects.filter(
        organization=org,
        hfl_user=user,
        occurred_at__date__gte=start,
        occurred_at__date__lte=end,
    )
    q_and_a_requests = period_rows.count()
    period_totals = period_rows.aggregate(
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
        cached_tokens=Sum("cached_tokens"),
        reasoning_tokens=Sum("reasoning_tokens"),
        total_tokens=Sum("total_tokens"),
        model_calls=Sum("model_calls"),
        total_estimated_cost=Sum("estimated_cost"),
        costed_requests=Count("estimated_cost"),
        last_source_sync_at=Max("source_synced_at"),
    )
    same_day = start == end
    bucket_expression = (
        TruncHour("occurred_at") if same_day else TruncDate("occurred_at")
    )
    trend_rows = (
        period_rows.annotate(bucket=bucket_expression)
        .values("bucket")
        .annotate(
            request_count=Count("id"),
            costed_requests=Count("estimated_cost"),
            total_calls=Sum("model_calls"),
            total_prompt_tokens=Sum("prompt_tokens"),
            total_completion_tokens=Sum("completion_tokens"),
            total_cached_tokens=Sum("cached_tokens"),
            total_reasoning_tokens=Sum("reasoning_tokens"),
            total_tokens=Sum("total_tokens"),
            total_cost=Sum("estimated_cost"),
        )
        .order_by("bucket")
    )
    trend_index = {
        row["bucket"].isoformat(): row
        for row in trend_rows
        if row["bucket"] is not None
    }
    trend = []
    if same_day:
        start_of_day = timezone.make_aware(
            datetime.combine(start, datetime.min.time()),
            timezone.get_current_timezone(),
        )
        final_hour = 23
        if end == timezone.localdate():
            final_hour = timezone.localtime().hour
        for hour in range(final_hour + 1):
            cursor = start_of_day + timedelta(hours=hour)
            bucket = cursor.isoformat()
            row = trend_index.get(bucket) or {}
            trend.append(_trend_item(bucket, row))
    else:
        cursor = start
        while cursor <= end:
            bucket = cursor.isoformat()
            row = trend_index.get(bucket) or {}
            trend.append(_trend_item(bucket, row))
            cursor += timedelta(days=1)
    by_source = (
        period_rows.values("backup_source_name")
        .annotate(
            q_and_a_requests=Count("id"),
            costed_requests=Count("estimated_cost"),
            model_calls=Sum("model_calls"),
            total_tokens=Sum("total_tokens"),
            total_estimated_cost=Sum("estimated_cost"),
        )
        .order_by(
            OrderBy(F("total_estimated_cost"), descending=True, nulls_last=True),
            "-total_tokens",
            "backup_source_name",
        )
    )

    costed_requests = int(period_totals["costed_requests"] or 0)
    if q_and_a_requests == 0:
        total_cost: float | None = 0
    elif (
        costed_requests == q_and_a_requests
        and period_totals["total_estimated_cost"] is not None
    ):
        total_cost = float(period_totals["total_estimated_cost"])
    else:
        total_cost = None
    pending_runs = period_rows.exclude(
        run_status__in=TERMINAL_RUN_STATUSES,
    ).count()
    return {
        "period": {"start_date": start_date, "end_date": end_date},
        "summary": {
            "estimated_cost": total_cost,
            "cost_currency": "USD",
            "total_tokens": int(period_totals["total_tokens"] or 0),
            "prompt_tokens": int(period_totals["prompt_tokens"] or 0),
            "completion_tokens": int(period_totals["completion_tokens"] or 0),
            "cached_tokens": int(period_totals["cached_tokens"] or 0),
            "reasoning_tokens": int(period_totals["reasoning_tokens"] or 0),
            "model_calls": int(period_totals["model_calls"] or 0),
            "q_and_a_requests": q_and_a_requests,
            "average_cost_per_q_and_a": (
                total_cost / q_and_a_requests
                if q_and_a_requests and total_cost is not None
                else (0 if not q_and_a_requests else None)
            ),
        },
        "trend": trend,
        "by_backup_source": [
            {
                "backup_source_name": row["backup_source_name"] or "Backup Source",
                "q_and_a_requests": row["q_and_a_requests"],
                "model_calls": row["model_calls"] or 0,
                "total_tokens": row["total_tokens"] or 0,
                "estimated_cost": (
                    float(row["total_estimated_cost"])
                    if (
                        row["total_estimated_cost"] is not None
                        and row["costed_requests"] == row["q_and_a_requests"]
                    )
                    else None
                ),
            }
            for row in by_source
        ],
        "backup_sources": list(
            period_rows.exclude(backup_source_name="")
            .order_by("backup_source_name")
            .values_list("backup_source_name", flat=True)
            .distinct()
        ),
        "results": results,
        "total": total,
        "page": page,
        "page_size": page_size,
        "data_freshness": {
            "last_source_sync_at": period_totals["last_source_sync_at"],
            "pending_runs": pending_runs,
        },
    }


def usage_detail(org, user, run_uuid: uuid.UUID) -> dict[str, Any]:
    row = (
        LensUsageLedger.objects.select_related("session_link")
        .filter(
            organization=org,
            hfl_user=user,
            sl_run_uuid=run_uuid,
        )
        .first()
    )
    if row is None:
        raise NotFound()
    payload = _ledger_item(row)
    payload.update(
        {
            "snapshot_created_at": row.snapshot_created_at,
            "source_scopes": list(row.source_scopes_json or []),
            "gateway_mode": row.gateway_selection_mode,
            "gateway_name": row.gateway_name,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "error": row.run_error,
            "call_details": list(row.call_details_json or []),
        }
    )
    return payload
