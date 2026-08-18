from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.lens_bridge.services.teardown_claims import (
    PROVISION_CLAIM_TTL_SECONDS,
    PROVISION_TASK_HARD_LIMIT_SECONDS,
    TEARDOWN_CLAIM_TTL_SECONDS,
    TEARDOWN_TASK_HARD_LIMIT_SECONDS,
)
from common.observability.celery_context import celery_trace

logger = logging.getLogger(__name__)

_PROVISION_TIME_LIMIT = PROVISION_TASK_HARD_LIMIT_SECONDS
_PROVISION_SOFT_LIMIT = max(60, _PROVISION_TIME_LIMIT - 300)
_TEARDOWN_TIME_LIMIT = TEARDOWN_TASK_HARD_LIMIT_SECONDS
_TEARDOWN_SOFT_LIMIT = max(60, _TEARDOWN_TIME_LIMIT - 300)
_CHAT_PROVISION_WAIT_SECONDS = 5


@shared_task(
    name="apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_provision_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 1},
    soft_time_limit=_PROVISION_SOFT_LIMIT,
    time_limit=_PROVISION_TIME_LIMIT,
)
def execute_copilot_chat_provision_task(
    self,
    *,
    session_link_id: int,
    expected_generation: int | None = None,
    expected_poll_sequence: int | None = None,
) -> dict:
    with celery_trace(
        f"copilot-provision-{session_link_id}",
        task_name="apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_provision_task",
    ):
        from apps.lens_bridge.services.chat_lifecycle import run_copilot_chat_provision

        logger.info("copilot chat provision celery started session_link_id=%s", session_link_id)
        result = run_copilot_chat_provision(
            session_link_id=int(session_link_id),
            expected_generation=expected_generation,
            expected_poll_sequence=expected_poll_sequence,
        )
        next_poll = result.get("next_poll")
        if result.get("status") == "waiting" and isinstance(next_poll, dict):
            retry_after_seconds = max(
                _CHAT_PROVISION_WAIT_SECONDS,
                int(next_poll.get("retry_after_seconds") or 0),
            )
            try:
                self.apply_async(
                    kwargs={
                        "session_link_id": int(session_link_id),
                        "expected_generation": int(next_poll["generation"]),
                        "expected_poll_sequence": int(next_poll["sequence"]),
                    },
                    countdown=retry_after_seconds,
                )
            except Exception:
                logger.exception(
                    "copilot provision follow-up dispatch failed; durable reconciler will retry "
                    "session_link_id=%s generation=%s sequence=%s",
                    session_link_id,
                    next_poll.get("generation"),
                    next_poll.get("sequence"),
                )
        logger.info(
            "copilot chat provision celery finished session_link_id=%s status=%s",
            session_link_id,
            result.get("status"),
        )
        return result


@shared_task(
    name="apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_teardown_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    soft_time_limit=_TEARDOWN_SOFT_LIMIT,
    time_limit=_TEARDOWN_TIME_LIMIT,
)
def execute_copilot_chat_teardown_task(self, *, session_link_id: int) -> dict:
    with celery_trace(
        f"copilot-teardown-{session_link_id}",
        task_name="apps.lens_bridge.tasks.chat_lifecycle.execute_copilot_chat_teardown_task",
    ):
        from apps.lens_bridge.services.chat_lifecycle import run_copilot_chat_teardown

        logger.info("copilot chat teardown celery started session_link_id=%s", session_link_id)
        result = run_copilot_chat_teardown(session_link_id=int(session_link_id))
        logger.info(
            "copilot chat teardown celery finished session_link_id=%s status=%s",
            session_link_id,
            result.get("status"),
        )
        return result


@shared_task(
    name=(
        "apps.lens_bridge.tasks.chat_lifecycle."
        "reconcile_copilot_chat_provisions_task"
    ),
)
def reconcile_copilot_chat_provisions_task(*, limit: int = 100) -> dict:
    """Requeue Chat provisioning work whose durable lease is absent or stale."""
    from apps.lens_bridge.models import LensSessionLink

    now = timezone.now()
    stale_claim = now - timedelta(seconds=PROVISION_CLAIM_TTL_SECONDS)
    sessions = list(
        LensSessionLink.objects.filter(
            lifecycle_status=LensSessionLink.LifecycleStatus.PROVISIONING,
        )
        .filter(
            Q(provision_next_retry_at__isnull=True)
            | Q(provision_next_retry_at__lte=now)
        )
        .filter(
            Q(provision_claimed_at__isnull=True)
            | Q(provision_claimed_at__lte=stale_claim)
        )
        .order_by("provision_next_retry_at", "id")
        .values_list("id", "provision_generation", "provision_poll_sequence")[
            : max(1, min(int(limit), 500))
        ]
    )
    queued_session_ids: list[int] = []
    failures: list[dict[str, str | int]] = []
    for session_id, generation, poll_sequence in sessions:
        try:
            execute_copilot_chat_provision_task.delay(
                session_link_id=session_id,
                expected_generation=generation,
                expected_poll_sequence=poll_sequence,
            )
            queued_session_ids.append(session_id)
        except Exception as exc:
            logger.exception(
                "copilot provision reconcile dispatch failed session_link_id=%s",
                session_id,
            )
            failures.append(
                {
                    "resource": "session",
                    "id": session_id,
                    "error": str(exc)[:1000],
                }
            )
    return {
        "queued": len(queued_session_ids),
        "session_ids": queued_session_ids,
        "failed": failures,
    }


@shared_task(
    name="apps.lens_bridge.tasks.chat_lifecycle.reconcile_lens_resource_teardowns_task",
)
def reconcile_lens_resource_teardowns_task(*, limit: int = 100) -> dict:
    """Requeue durable Chat and Knowledge Source teardowns that are due."""

    from apps.lens_bridge.models import LensSessionLink

    now = timezone.now()
    stale_claim = now - timedelta(seconds=TEARDOWN_CLAIM_TTL_SECONDS)
    session_ids = list(
        LensSessionLink.objects.filter(
            (
                (
                    Q(
                        lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
                        cleanup_intent=LensSessionLink.CleanupIntent.DELETE_SESSION,
                    )
                    | Q(
                        lifecycle_status=LensSessionLink.LifecycleStatus.FAILED,
                        cleanup_intent=LensSessionLink.CleanupIntent.RESET_FOR_RETRY,
                    )
                )
                & Q(
                    cleanup_status__in=(
                        LensSessionLink.CleanupStatus.PENDING,
                        LensSessionLink.CleanupStatus.RUNNING,
                        LensSessionLink.CleanupStatus.BLOCKED,
                    )
                )
            )
            | Q(
                lifecycle_status=LensSessionLink.LifecycleStatus.DELETING,
                cleanup_intent=LensSessionLink.CleanupIntent.NONE,
            )
        )
        .filter(
            Q(teardown_next_retry_at__isnull=True)
            | Q(teardown_next_retry_at__lte=now)
        )
        .filter(Q(teardown_claimed_at__isnull=True) | Q(teardown_claimed_at__lte=stale_claim))
        .order_by("teardown_next_retry_at", "id")
        .values_list("id", flat=True)[: max(1, min(int(limit), 500))]
    )
    queued_session_ids: list[int] = []
    failures: list[dict[str, str | int]] = []
    for session_id in session_ids:
        try:
            execute_copilot_chat_teardown_task.delay(session_link_id=session_id)
            queued_session_ids.append(session_id)
        except Exception as exc:
            logger.exception(
                "copilot teardown reconcile dispatch failed session_link_id=%s",
                session_id,
            )
            failures.append(
                {
                    "resource": "session",
                    "id": session_id,
                    "error": str(exc)[:1000],
                }
            )
    from apps.lens_bridge.tasks.knowledge_source_teardown import (
        due_knowledge_source_teardown_ids,
        execute_knowledge_source_teardown_task,
    )

    knowledge_source_ids = due_knowledge_source_teardown_ids(
        limit=limit,
        now=now,
    )
    queued_knowledge_source_ids: list[int] = []
    for knowledge_source_id in knowledge_source_ids:
        try:
            execute_knowledge_source_teardown_task.delay(
                knowledge_source_id=knowledge_source_id
            )
            queued_knowledge_source_ids.append(knowledge_source_id)
        except Exception as exc:
            logger.exception(
                "knowledge source teardown reconcile dispatch failed knowledge_source_id=%s",
                knowledge_source_id,
            )
            failures.append(
                {
                    "resource": "knowledge_source",
                    "id": knowledge_source_id,
                    "error": str(exc)[:1000],
                }
            )
    return {
        "queued": len(queued_session_ids) + len(queued_knowledge_source_ids),
        "session_ids": queued_session_ids,
        "knowledge_source_ids": queued_knowledge_source_ids,
        "failed": failures,
    }
