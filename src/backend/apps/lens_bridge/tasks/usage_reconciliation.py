from __future__ import annotations

import logging
import uuid
from typing import Any

from celery import shared_task
from django.utils import timezone

from common.observability.celery_context import celery_trace


logger = logging.getLogger(__name__)


@shared_task(
    name="apps.lens_bridge.tasks.usage_reconciliation.diagnose_provider_error_task",
    soft_time_limit=50,
    time_limit=60,
)
def diagnose_provider_error_task(*, ledger_id: int, claim_token: str) -> str:
    from apps.lens_bridge.services.provider_errors import diagnose_claimed_ledger

    return diagnose_claimed_ledger(ledger_id=ledger_id, claim_token=claim_token)


def _dispatch_provider_diagnostics(limit: int) -> int:
    from apps.lens_bridge.services.provider_errors import (
        claim_due_diagnostics, defer_diagnostic,
    )

    queued = 0
    for ledger_id, token in claim_due_diagnostics(limit=limit):
        try:
            diagnose_provider_error_task.delay(ledger_id=ledger_id, claim_token=token)
            queued += 1
        except Exception:
            logger.warning("Provider diagnostic dispatch deferred ledger_id=%s", ledger_id)
            defer_diagnostic(ledger_id, token)
    return queued


@shared_task(
    name=(
        "apps.lens_bridge.tasks.usage_reconciliation."
        "execute_usage_ledger_reconciliation_task"
    ),
    soft_time_limit=90,
    time_limit=100,
)
def execute_usage_ledger_reconciliation_task(
    *,
    ledger_id: int,
    claim_token: str,
) -> dict[str, Any]:
    """Reconcile one independently claimed usage ledger row."""

    from apps.lens_bridge.services.usage import reconcile_claimed_usage_ledger

    with celery_trace(
        f"usage-ledger-reconciliation-{ledger_id}",
        task_name=(
            "apps.lens_bridge.tasks.usage_reconciliation."
            "execute_usage_ledger_reconciliation_task"
        ),
    ):
        result = reconcile_claimed_usage_ledger(
            ledger_id=int(ledger_id),
            claim_token=uuid.UUID(str(claim_token)),
        )
        logger.info(
            "usage ledger reconciliation finished ledger_id=%s status=%s",
            ledger_id,
            result["status"],
        )
        return result


@shared_task(
    name=("apps.lens_bridge.tasks.usage_reconciliation.reconcile_usage_ledgers_task"),
)
def reconcile_usage_ledgers_task(*, limit: int = 100) -> dict[str, Any]:
    """Dispatch due reconciliations without remote I/O in the dispatcher."""

    from apps.lens_bridge.services.usage import (
        claim_due_usage_ledgers,
        record_reconciliation_failure,
        seed_missing_active_run_ledgers,
    )

    seeded = seed_missing_active_run_ledgers(limit=limit)
    claims = claim_due_usage_ledgers(limit=limit, now=timezone.now())
    queued: list[int] = []
    failed: list[dict[str, str | int]] = []
    for ledger_id, claim_token in claims:
        try:
            execute_usage_ledger_reconciliation_task.delay(
                ledger_id=ledger_id,
                claim_token=str(claim_token),
            )
            queued.append(ledger_id)
        except Exception as exc:
            logger.exception(
                "usage reconciliation dispatch failed ledger_id=%s",
                ledger_id,
            )
            record_reconciliation_failure(
                ledger_id=ledger_id,
                claim_token=claim_token,
                message=str(exc),
                now=timezone.now(),
            )
            failed.append({"ledger_id": ledger_id, "error": str(exc)[:1000]})
    return {
        "seeded": seeded,
        "claimed": len(claims),
        "queued": queued,
        "failed": failed,
        "provider_diagnostics_queued": _dispatch_provider_diagnostics(limit),
    }
