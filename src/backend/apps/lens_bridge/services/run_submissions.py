"""Durable SourceLens Run submission and recovery orchestration."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.lens_bridge.models import LensRunSubmission, LensSessionLink
from apps.lens_bridge.services import copilot, sl_client, usage


_CLAIM_TIMEOUT = timedelta(minutes=2)
_MAX_RETRY_DELAY_SECONDS = 5 * 60


class RunSubmissionConflictError(Exception):
    """Raised when a session already owns different active or pending work."""


class RunSubmissionInvalidError(Exception):
    """Raised when a durable submission can no longer be executed safely."""


class RunSubmissionClaimLostError(Exception):
    """Raised when another recovery worker owns the current submission claim."""


class RunSubmissionContractError(sl_client.LensBridgeError):
    """Raised when SourceLens returns a Run payload that cannot be bound safely."""


def _retry_delay(attempts: int) -> timedelta:
    seconds = min(5 * (2 ** min(max(attempts - 1, 0), 6)), _MAX_RETRY_DELAY_SECONDS)
    return timedelta(seconds=seconds)


def _normalized_attachment_uuids(values) -> list[str]:
    return [str(value) for value in (values or [])]


def _submission_for_active_run(
    link: LensSessionLink,
) -> LensRunSubmission | None:
    if link.active_run_uuid is None:
        return None
    return LensRunSubmission.objects.filter(
        session_link=link,
        sl_run_uuid=link.active_run_uuid,
    ).first()


def _fetch_submission_run(submission: LensRunSubmission) -> dict[str, Any]:
    if submission.sl_run_uuid is None or submission.hfl_user is None:
        raise RunSubmissionInvalidError(
            "Run submission is missing its SourceLens binding."
        )
    return sl_client.request_json(
        "GET",
        f"/api/lens/runs/{submission.sl_run_uuid}/",
        hfl_user=submission.hfl_user,
    )


@transaction.atomic
def prepare_submission(
    link: LensSessionLink,
    *,
    question: str,
    idempotency_key: str,
    attachment_uuids: list[str] | None = None,
) -> tuple[LensRunSubmission, dict[str, Any] | None]:
    """Persist one submission before SourceLens receives the request.

    The caller must hold the SourceLens Run creation guard. The returned Run
    payload is non-null when the same idempotency key was already bound.
    """

    normalized_attachments = _normalized_attachment_uuids(attachment_uuids)
    link = (
        LensSessionLink.objects.select_for_update()
        .select_related("organization", "hfl_user")
        .get(pk=link.pk)
    )
    active_run = copilot.resolve_active_run(link)
    if active_run is not None:
        active_submission = _submission_for_active_run(link)
        active_key = str(active_run.get("idempotency_key") or "")
        if active_submission is not None:
            active_key = active_submission.idempotency_key
        if active_key == idempotency_key:
            if active_submission is not None and (
                active_submission.question != question
                or _normalized_attachment_uuids(
                    active_submission.attachment_uuids
                )
                != normalized_attachments
            ):
                raise RunSubmissionConflictError(
                    "The idempotency key is already associated with another request."
                )
            if active_submission is None:
                active_submission = LensRunSubmission.objects.create(
                    organization=link.organization,
                    hfl_user=link.hfl_user,
                    session_link=link,
                    idempotency_key=idempotency_key,
                    question=question,
                    attachment_uuids=normalized_attachments,
                    status=LensRunSubmission.Status.BOUND,
                    sl_run_uuid=link.active_run_uuid,
                    run_status=str(active_run.get("status") or link.active_run_status),
                )
            return active_submission, active_run
        raise RunSubmissionConflictError("This chat already has an active response.")

    existing = LensRunSubmission.objects.filter(
        session_link=link,
        idempotency_key=idempotency_key,
    ).first()
    if existing is not None:
        if (
            existing.question != question
            or _normalized_attachment_uuids(existing.attachment_uuids)
            != normalized_attachments
        ):
            raise RunSubmissionConflictError(
                "The idempotency key is already associated with another request."
            )
        if existing.status == LensRunSubmission.Status.BOUND:
            return existing, _fetch_submission_run(existing)
        if existing.status == LensRunSubmission.Status.FAILED:
            raise RunSubmissionConflictError(
                "The idempotency key belongs to a failed submission."
            )
        return existing, None

    if LensRunSubmission.objects.filter(
        session_link=link,
        status=LensRunSubmission.Status.PENDING,
    ).exists():
        raise RunSubmissionConflictError(
            "This chat has a response submission awaiting recovery."
        )

    from apps.subscription.services.interface import enforce_license_quota

    # Reject new work at the quota boundary; SourceLens remains authoritative
    # for the final token measurement after the Run completes.
    enforce_license_quota(link.organization, "ai_tokens", additional=0)
    submission = LensRunSubmission.objects.create(
        organization=link.organization,
        hfl_user=link.hfl_user,
        session_link=link,
        idempotency_key=idempotency_key,
        question=question,
        attachment_uuids=normalized_attachments,
        recovery_next_at=timezone.now(),
    )
    return submission, None


@transaction.atomic
def execute_submission(
    submission_id: int,
    *,
    claim_token: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Create or replay a pending SourceLens Run and bind it atomically in HFL.

    The caller must hold the SourceLens Run creation guard. SourceLens
    idempotency makes this safe after an uncertain transport or process failure.
    """

    submission = (
        LensRunSubmission.objects.select_for_update(of=("self",))
        .select_related(
            "hfl_user",
            "organization",
            "session_link__organization",
            "session_link__hfl_user",
        )
        .get(pk=submission_id)
    )
    if submission.status == LensRunSubmission.Status.BOUND:
        return _fetch_submission_run(submission)
    if claim_token is not None and submission.recovery_claim_token != claim_token:
        raise RunSubmissionClaimLostError(
            "Run submission recovery claim is no longer valid."
        )
    if submission.status != LensRunSubmission.Status.PENDING:
        raise RunSubmissionInvalidError("Run submission is no longer recoverable.")

    link = LensSessionLink.objects.select_for_update().get(
        pk=submission.session_link_id
    )
    if (
        link.status != LensSessionLink.Status.ACTIVE
        or link.lifecycle_status != LensSessionLink.LifecycleStatus.READY
        or link.sl_session_uuid is None
        or submission.hfl_user is None
    ):
        raise RunSubmissionInvalidError("The chat is no longer ready for Run recovery.")
    if link.active_run_uuid is not None:
        active_submission = _submission_for_active_run(link)
        if active_submission is None or active_submission.pk != submission.pk:
            raise RunSubmissionConflictError(
                "This chat already has an active response."
            )

    run_body = {
        "question": submission.question,
        "idempotency_key": submission.idempotency_key,
    }
    attachment_uuids = _normalized_attachment_uuids(
        submission.attachment_uuids
    )
    if attachment_uuids:
        run_body["attachment_uuids"] = attachment_uuids
    data = sl_client.request_json(
        "POST",
        f"/api/lens/sessions/{link.sl_session_uuid}/runs/",
        json_body=run_body,
        hfl_user=submission.hfl_user,
    )
    raw_run_uuid = data.get("uuid") if isinstance(data, dict) else None
    if not raw_run_uuid:
        raise RunSubmissionContractError(
            "SourceLens Run response omitted its UUID."
        )
    try:
        run_uuid = uuid.UUID(str(raw_run_uuid))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RunSubmissionContractError(
            "SourceLens Run response returned an invalid UUID."
        ) from exc
    returned_key = str(data.get("idempotency_key") or "")
    if returned_key and returned_key != submission.idempotency_key:
        raise RunSubmissionContractError(
            "SourceLens Run response returned an inconsistent idempotency key."
        )

    run_status = str(data.get("status") or "queued")
    submission.status = LensRunSubmission.Status.BOUND
    submission.sl_run_uuid = run_uuid
    submission.run_status = run_status
    submission.last_error = ""
    submission.recovery_claim_token = None
    submission.recovery_claimed_at = None
    submission.recovery_next_at = None
    submission.save(
        update_fields=[
            "status",
            "sl_run_uuid",
            "run_status",
            "last_error",
            "recovery_claim_token",
            "recovery_claimed_at",
            "recovery_next_at",
            "updated_at",
        ]
    )

    link.last_message_at = timezone.now()
    link.save(update_fields=["last_message_at", "updated_at"])
    usage.register_usage_run(
        link,
        run_uuid=run_uuid,
        question=submission.question,
        status=run_status,
    )
    if run_status in copilot.TERMINAL_RUN_STATUSES:
        usage.capture_run_usage(link, data)
    else:
        copilot.set_active_run(link, run_uuid=run_uuid, status=run_status)
    return data


def record_submission_error(
    submission_id: int,
    error: Exception,
    *,
    retryable: bool,
) -> None:
    """Record a failed attempt without losing an uncertain submission."""

    with transaction.atomic():
        submission = (
            LensRunSubmission.objects.select_for_update()
            .filter(pk=submission_id, status=LensRunSubmission.Status.PENDING)
            .first()
        )
        if submission is None:
            return
        submission.recovery_attempts += 1
        submission.last_error = str(error)[:1000]
        submission.recovery_claim_token = None
        submission.recovery_claimed_at = None
        if retryable:
            submission.recovery_next_at = timezone.now() + _retry_delay(
                submission.recovery_attempts
            )
        else:
            submission.status = LensRunSubmission.Status.FAILED
            submission.recovery_next_at = None
        submission.save(
            update_fields=[
                "status",
                "last_error",
                "recovery_attempts",
                "recovery_claim_token",
                "recovery_claimed_at",
                "recovery_next_at",
                "updated_at",
            ]
        )


def defer_claimed_submission(
    submission_id: int,
    claim_token: uuid.UUID,
    *,
    delay: timedelta = timedelta(seconds=15),
) -> None:
    """Release a recovery claim when maintenance temporarily blocks execution."""

    LensRunSubmission.objects.filter(
        pk=submission_id,
        status=LensRunSubmission.Status.PENDING,
        recovery_claim_token=claim_token,
    ).update(
        recovery_claim_token=None,
        recovery_claimed_at=None,
        recovery_next_at=timezone.now() + delay,
        updated_at=timezone.now(),
    )


def claim_due_submissions(
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> list[tuple[int, uuid.UUID]]:
    """Claim due pending submissions for independent recovery workers."""

    current = now or timezone.now()
    stale_before = current - _CLAIM_TIMEOUT
    bounded_limit = max(1, min(int(limit), 500))
    claims: list[tuple[int, uuid.UUID]] = []
    with transaction.atomic():
        rows = list(
            LensRunSubmission.objects.select_for_update(skip_locked=True)
            .filter(status=LensRunSubmission.Status.PENDING)
            .filter(Q(recovery_next_at__isnull=True) | Q(recovery_next_at__lte=current))
            .filter(
                Q(recovery_claim_token__isnull=True)
                | Q(recovery_claimed_at__lt=stale_before)
            )
            .order_by("recovery_next_at", "created_at", "id")[:bounded_limit]
        )
        for submission in rows:
            token = uuid.uuid4()
            submission.recovery_claim_token = token
            submission.recovery_claimed_at = current
            submission.save(
                update_fields=[
                    "recovery_claim_token",
                    "recovery_claimed_at",
                    "updated_at",
                ]
            )
            claims.append((submission.id, token))
    return claims
