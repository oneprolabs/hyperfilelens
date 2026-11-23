"""Bounded, read-only provider diagnostics for terminal Q&A runs.

Run.error can lose the provider's reason. SourceLens's supported admin usage
API retains it. Match both the Run UUID and call UUID before interpreting an
error; time windows alone are never evidence. Nothing here invokes a model.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.lens_bridge.models import LensOrgModelLink, LensUsageLedger
from apps.lens_bridge.services import sl_client

logger = logging.getLogger(__name__)

INSUFFICIENT_CREDIT = "MODEL_INSUFFICIENT_CREDIT"
CREDIT_MESSAGE = (
    "The AI service is unavailable due to an account issue. "
    "Please contact your administrator."
)
MAX_ATTEMPTS = 3
MAX_PAGES = 3
PAGE_SIZE = 100
MAX_RECOVERY_RUNS = 3
DIAGNOSTIC_BUDGET_SECONDS = 30
CLAIM_SECONDS = 120
_UNKNOWN_MODEL_ERRORS = frozenset({
    "EMPTY_AGENT_RESPONSE", "MODEL_UNAVAILABLE", "MODEL_PROVIDER_ERROR",
})


def _date(value: object) -> datetime | None:
    try:
        parsed = parse_datetime(str(value or ""))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed is not None and timezone.is_aware(parsed) else None


def _uuid(value: object) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def credit_failure_message(state: dict[str, Any]) -> str | None:
    return CREDIT_MESSAGE if (
        state.get("status") == "confirmed"
        and state.get("code") == INSUFFICIENT_CREDIT
    ) else None


def _explicit_insufficient_credit(error: object) -> bool:
    """Only inspect a correlated provider error, never user/assistant content."""
    if not isinstance(error, str):
        return False
    text = error.casefold()
    return any(marker in text for marker in (
        "当前租户 credit 账户余额不足",
        "insufficient_balance",
        "insufficient_credit",
        "insufficient credit",
        "insufficient account balance",
        "credit balance is too low",
    ))


def _active_alerts():
    from apps.alert.constants import AlertStatus, ResourceType
    from apps.alert.models import AlertRecord

    return AlertRecord.objects.filter(
        resource_type=ResourceType.AI_MODEL,
        metadata__provider_error_code=INSUFFICIENT_CREDIT,
        status__in=(AlertStatus.FIRING, AlertStatus.ACKNOWLEDGED),
    )


def initialize_diagnostic(row: LensUsageLedger) -> None:
    """Prepare durable work; the existing periodic dispatcher claims it later."""
    if row.provider_error_state_json:
        return
    failed = row.run_status == "failed" and row.run_error.strip().upper() in _UNKNOWN_MODEL_ERRORS
    recovery = row.run_status in {"done", "success", "completed"} and (
        _active_alerts().filter(
            Q(organization_id__isnull=True) | Q(organization_id=row.organization_id),
        ).exists()
        # A real answer can arrive while an earlier failure is still being
        # diagnosed. Track that evidence too; do not open a stale incident later.
        or LensUsageLedger.objects.filter(
            provider_error_state_json__status="pending",
            provider_error_state_json__mode="failure",
        ).exists()
    )
    if failed or recovery:
        row.provider_error_state_json = {
            "status": "pending", "attempts": 0,
            "next_at": timezone.now().isoformat(),
            "mode": "failure" if failed else "recovery",
        }


def claim_due_diagnostics(*, limit: int = 50) -> list[tuple[int, str]]:
    """Lease work independently from token-metering reconciliation."""
    now = timezone.now()
    claims = []
    with transaction.atomic():
        rows = LensUsageLedger.objects.select_for_update(skip_locked=True).filter(
            provider_error_state_json__status="pending",
            provider_error_state_json__next_at__lte=now.isoformat(),
        ).order_by("id")[:max(1, min(limit, 100))]
        for row in rows:
            state = dict(row.provider_error_state_json)
            attempts = int(state.get("attempts") or 0)
            if attempts >= MAX_ATTEMPTS:
                state.update(status="unconfirmed")
            else:
                token = str(uuid.uuid4())
                state.update(
                    attempts=attempts + 1, claim_token=token,
                    next_at=(now + timedelta(seconds=CLAIM_SECONDS)).isoformat(),
                )
                claims.append((row.pk, token))
            row.provider_error_state_json = state
            row.save(update_fields=["provider_error_state_json"])
    return claims


def defer_diagnostic(ledger_id: int, claim_token: str) -> None:
    """Do not retain exception strings: provider responses may contain secrets."""
    with transaction.atomic():
        row = LensUsageLedger.objects.select_for_update().filter(pk=ledger_id).first()
        if row is None:
            return
        state = dict(row.provider_error_state_json)
        if state.get("claim_token") != claim_token:
            return
        state.pop("claim_token", None)
        attempts = int(state.get("attempts") or 0)
        state.update(
            status="unconfirmed" if attempts >= MAX_ATTEMPTS else "pending",
            next_at=(timezone.now() + timedelta(seconds=30 * attempts)).isoformat(),
        )
        row.provider_error_state_json = state
        row.save(update_fields=["provider_error_state_json"])


def _usage_for_run(
    row: LensUsageLedger, run: dict, *, deadline: float,
) -> tuple[list[dict], bool]:
    """Paginate within a bounded window, then verify both durable identifiers."""
    expected = {
        ref for call in run.get("model_calls") or []
        if isinstance(call, dict) and (ref := _uuid(call.get("uuid")))
    }
    if not expected:
        return [], False
    started = _date(run.get("started_at")) or row.started_at
    finished = _date(run.get("finished_at")) or row.finished_at
    if started is None or finished is None or finished < started:
        return [], False
    results = []
    found = set()
    for page in range(1, MAX_PAGES + 1):
        query = urlencode({
            "start_date": (started - timedelta(seconds=5)).isoformat(),
            "end_date": (finished + timedelta(seconds=30)).isoformat(),
            "page_size": PAGE_SIZE, "page": page,
        })
        response = sl_client.request_json(
            "GET", "/api/v1/admin/llm-usage/?" + query,
            timeout=10, deadline=deadline,
        )
        if not isinstance(response, dict) or not isinstance(response.get("results"), list):
            raise ValueError("Invalid usage response")
        items = response["results"]
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata")
            if not isinstance(metadata, dict):
                continue
            call_id = _uuid(item.get("id"))
            if (
                call_id not in expected or call_id in found
                or _uuid(metadata.get("run_uuid")) != str(row.sl_run_uuid)
            ):
                continue
            found.add(call_id)
            results.append(item)
        if found == expected or len(items) < PAGE_SIZE:
            break
    return results, found == expected


def _owned_model(row: LensUsageLedger, item: dict) -> LensOrgModelLink | None:
    ref = _uuid((item.get("metadata") or {}).get("selected_model_ref"))
    if not ref:
        return None
    return LensOrgModelLink.objects.filter(
        Q(scope="platform", organization__isnull=True)
        | Q(scope="organization", organization_id=row.organization_id),
        sl_config_uuid=ref, is_deleted=False,
    ).first()


def _read_run(row: LensUsageLedger, *, deadline: float) -> dict:
    run = sl_client.request_json(
        "GET", f"/api/lens/admin/runs/{row.sl_run_uuid}/",
        timeout=10, deadline=deadline,
    )
    if not isinstance(run, dict) or _uuid(run.get("uuid")) != str(row.sl_run_uuid):
        raise ValueError("Mismatched run")
    return run


def _recovery_call(
    row: LensUsageLedger, run: dict, items: list[dict], *, complete: bool,
) -> tuple[LensOrgModelLink, dict] | None:
    if (
        not complete or run.get("status") != "done"
        or not isinstance(run.get("answer"), str) or not run["answer"].strip()
    ):
        return None
    # A successful fallback to another config does not recover this one.
    successful = [
        item for item in items if item.get("success") is True
        and isinstance(item.get("completion_tokens"), int)
        and not isinstance(item["completion_tokens"], bool)
        and item["completion_tokens"] > 0 and _date(item.get("created_at"))
    ]
    if successful:
        item = max(successful, key=lambda value: _date(value["created_at"]))
        model = _owned_model(row, item)
        if model is not None:
            return model, item
    return None


def _check_later_recoveries(
    row: LensUsageLedger, evidence: list, *, deadline: float,
) -> None:
    """Check a few already-completed runs before opening a historical incident.

    A terminal failure may arrive after a later answer was captured, before any
    pending diagnosis or incident existed. Do not diagnose every healthy answer:
    use the same bounded REST budget to verify recent recovery candidates here.
    """
    observed: dict[int, datetime] = {}
    for model, entry in evidence:
        seen_at = _date(entry["observed_at"])
        if seen_at is not None:
            observed[model.pk] = max(observed.get(model.pk, seen_at), seen_at)
    if not observed:
        return
    candidates = LensUsageLedger.objects.filter(
        run_status="done", finished_at__gt=min(observed.values()),
    ).filter(
        Q(provider_error_state_json={})
        | (
            Q(provider_error_state_json__mode="recovery")
            & ~Q(provider_error_state_json__status="checked")
        ),
    )
    if not any(model.scope == "platform" for model, _ in evidence):
        candidates = candidates.filter(organization_id=row.organization_id)
    for candidate in candidates.order_by("-finished_at", "-pk")[:MAX_RECOVERY_RUNS]:
        if time.monotonic() >= deadline:
            break
        try:
            run = _read_run(candidate, deadline=deadline)
            items, complete = _usage_for_run(candidate, run, deadline=deadline)
        except (sl_client.LensBridgeError, ValueError) as exc:
            # Optional recovery reads must not discard confirmed failure evidence.
            logger.warning(
                "Historical recovery check skipped ledger_id=%s exception_type=%s",
                candidate.pk, type(exc).__name__,
            )
            continue
        recovery = _recovery_call(candidate, run, items, complete=complete)
        if recovery is not None:
            model, item = recovery
            if model.pk in observed and _date(item["created_at"]) > observed[model.pk]:
                # No failed-ledger/model locks are held during this operation.
                # Persist the same watermark used by ordinary recovery jobs.
                _confirm_recovery(candidate.pk, None, model, item)


def diagnose_claimed_ledger(*, ledger_id: int, claim_token: str) -> str:
    row = LensUsageLedger.objects.filter(
        pk=ledger_id, provider_error_state_json__claim_token=claim_token,
    ).first()
    if row is None:
        return "skipped"
    try:
        deadline = time.monotonic() + DIAGNOSTIC_BUDGET_SECONDS
        run = _read_run(row, deadline=deadline)
        items, complete = _usage_for_run(row, run, deadline=deadline)
        mode = row.provider_error_state_json.get("mode")
        if mode == "failure" and run.get("status") == "failed":
            evidence = []
            for item in items:
                if item.get("success") is not False or not _explicit_insufficient_credit(item.get("error")):
                    continue
                model = _owned_model(row, item)
                if model is not None and (seen_at := _date(item.get("created_at"))):
                    evidence.append((model, {
                        "call_uuid": _uuid(item.get("id")),
                        "model_ref": str(model.sl_config_uuid),
                        "observed_at": seen_at.isoformat(),
                    }))
            if evidence:
                _check_later_recoveries(row, evidence, deadline=deadline)
                _confirm_failure(row.pk, claim_token, evidence)
                return "confirmed"
        elif mode == "recovery":
            recovery = _recovery_call(row, run, items, complete=complete)
            if recovery is not None:
                model, item = recovery
                _confirm_recovery(row.pk, claim_token, model, item)
                return "checked"
    except Exception as exc:
        logger.warning(
            "Provider diagnostics deferred ledger_id=%s exception_type=%s",
            ledger_id, type(exc).__name__,
        )
    defer_diagnostic(ledger_id, claim_token)
    return "unconfirmed"


def _confirm_failure(ledger_id: int, token: str, evidence: list) -> None:
    from apps.lens_bridge.services.provider_credit_alerts import fire_credit_alert

    with transaction.atomic():
        row = LensUsageLedger.objects.select_for_update().get(pk=ledger_id)
        state = dict(row.provider_error_state_json)
        if state.get("claim_token") != token or row.run_status != "failed":
            return
        state.pop("claim_token", None)
        state.update(
            status="confirmed", code=INSUFFICIENT_CREDIT,
            checked_at=timezone.now().isoformat(),
            evidence=[entry for _, entry in evidence],
        )
        row.provider_error_state_json = state
        row.save(update_fields=["provider_error_state_json"])
        # Lock model configurations in stable order; concurrent failed runs share
        # an incident but must never lose counts or send duplicate first alerts.
        for model_id in sorted({model.pk for model, _ in evidence}):
            model = LensOrgModelLink.objects.select_for_update().get(pk=model_id, is_deleted=False)
            if model.scope != "platform" and model.organization_id != row.organization_id:
                raise ValueError("Model ownership changed during diagnostics")
            model_evidence = [entry for owner, entry in evidence if owner.pk == model_id]
            fire_credit_alert(row, model, model_evidence)


def _confirm_recovery(
    ledger_id: int, token: str | None, model: LensOrgModelLink, item: dict,
) -> None:
    from apps.lens_bridge.services.provider_credit_alerts import recover_credit_alerts

    with transaction.atomic():
        row = LensUsageLedger.objects.select_for_update().get(pk=ledger_id)
        state = dict(row.provider_error_state_json)
        if row.run_status != "done":
            return
        if token is not None and state.get("claim_token") != token:
            return
        if token is None and state and state.get("mode") != "recovery":
            return
        model = LensOrgModelLink.objects.select_for_update().get(pk=model.pk, is_deleted=False)
        if model.scope != "platform" and model.organization_id != row.organization_id:
            raise ValueError("Model ownership changed during diagnostics")
        recover_credit_alerts(model, _date(item["created_at"]))
        state.pop("claim_token", None)
        state.update(
            status="checked", mode="recovery", checked_at=timezone.now().isoformat(),
            recovered_model_ref=str(model.sl_config_uuid),
            observed_at=_date(item["created_at"]).isoformat(),
        )
        row.provider_error_state_json = state
        row.save(update_fields=["provider_error_state_json"])
