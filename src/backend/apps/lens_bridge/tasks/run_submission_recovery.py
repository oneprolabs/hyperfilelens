"""Recovery tasks for durable HFL-to-SourceLens Run submissions."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from celery import shared_task

from common.observability.celery_context import celery_trace


logger = logging.getLogger(__name__)


@shared_task(
    name=(
        "apps.lens_bridge.tasks.run_submission_recovery."
        "execute_run_submission_recovery_task"
    ),
    soft_time_limit=90,
    time_limit=100,
)
def execute_run_submission_recovery_task(
    *,
    submission_id: int,
    claim_token: str,
) -> dict[str, Any]:
    """Replay one claimed submission with its original idempotency key."""

    from apps.lens_bridge.services import run_submissions, sl_client
    from apps.lens_bridge.services.maintenance import (
        sourcelens_maintenance_active,
        sourcelens_run_creation_guard,
    )

    token = uuid.UUID(str(claim_token))
    with celery_trace(
        f"run-submission-recovery-{submission_id}",
        task_name=(
            "apps.lens_bridge.tasks.run_submission_recovery."
            "execute_run_submission_recovery_task"
        ),
    ):
        try:
            with sourcelens_run_creation_guard():
                if sourcelens_maintenance_active():
                    run_submissions.defer_claimed_submission(submission_id, token)
                    return {"status": "deferred", "submission_id": submission_id}
                run = run_submissions.execute_submission(
                    submission_id,
                    claim_token=token,
                )
        except run_submissions.RunSubmissionContractError as exc:
            logger.error(
                "SourceLens Run submission contract failed submission_id=%s: %s",
                submission_id,
                exc,
            )
            run_submissions.record_submission_error(
                submission_id,
                exc,
                retryable=False,
            )
            return {"status": "failed", "submission_id": submission_id}
        except sl_client.LensBridgeError as exc:
            retryable = exc.status_code >= 500 or exc.status_code in {
                408,
                409,
                425,
                429,
            }
            run_submissions.record_submission_error(
                submission_id,
                exc,
                retryable=retryable,
            )
            return {
                "status": "retrying" if retryable else "failed",
                "submission_id": submission_id,
            }
        except run_submissions.RunSubmissionClaimLostError:
            return {"status": "skipped", "submission_id": submission_id}
        except (
            run_submissions.RunSubmissionConflictError,
            run_submissions.RunSubmissionInvalidError,
        ) as exc:
            run_submissions.record_submission_error(
                submission_id,
                exc,
                retryable=False,
            )
            return {"status": "failed", "submission_id": submission_id}
        except Exception as exc:
            logger.exception(
                "Run submission recovery failed submission_id=%s",
                submission_id,
            )
            run_submissions.record_submission_error(
                submission_id,
                exc,
                retryable=True,
            )
            return {"status": "retrying", "submission_id": submission_id}
        return {
            "status": str(run.get("status") or "bound"),
            "submission_id": submission_id,
            "run_uuid": str(run.get("uuid") or ""),
        }


@shared_task(
    name=(
        "apps.lens_bridge.tasks.run_submission_recovery.reconcile_run_submissions_task"
    ),
)
def reconcile_run_submissions_task(*, limit: int = 100) -> dict[str, Any]:
    """Claim due submissions and dispatch independent recovery workers."""

    from apps.lens_bridge.services import run_submissions

    claims = run_submissions.claim_due_submissions(limit=limit)
    queued: list[int] = []
    failed: list[dict[str, str | int]] = []
    for submission_id, claim_token in claims:
        try:
            execute_run_submission_recovery_task.delay(
                submission_id=submission_id,
                claim_token=str(claim_token),
            )
            queued.append(submission_id)
        except Exception as exc:
            logger.exception(
                "Run submission recovery dispatch failed submission_id=%s",
                submission_id,
            )
            run_submissions.record_submission_error(
                submission_id,
                exc,
                retryable=True,
            )
            failed.append({"submission_id": submission_id, "error": str(exc)[:1000]})
    return {
        "claimed": len(claims),
        "queued": queued,
        "failed": failed,
    }
