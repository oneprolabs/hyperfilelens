"""Celery entrypoints for durable Knowledge Source teardown."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.lens_bridge.services.teardown_claims import (
    TEARDOWN_CLAIM_TTL_SECONDS,
    TEARDOWN_TASK_HARD_LIMIT_SECONDS,
)
from common.observability.celery_context import celery_trace

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.lens_bridge.tasks.knowledge_source_teardown.execute_knowledge_source_teardown_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    soft_time_limit=max(60, TEARDOWN_TASK_HARD_LIMIT_SECONDS - 300),
    time_limit=TEARDOWN_TASK_HARD_LIMIT_SECONDS,
)
def execute_knowledge_source_teardown_task(
    self,
    *,
    knowledge_source_id: int,
) -> dict:
    from apps.lens_bridge.services.knowledge_source_teardown import (
        run_knowledge_source_teardown,
    )

    with celery_trace(
        f"knowledge-source-teardown-{knowledge_source_id}",
        task_name=(
            "apps.lens_bridge.tasks.knowledge_source_teardown."
            "execute_knowledge_source_teardown_task"
        ),
    ):
        logger.info(
            "knowledge source teardown celery started ks_id=%s",
            knowledge_source_id,
        )
        result = run_knowledge_source_teardown(
            knowledge_source_id=int(knowledge_source_id)
        )
        logger.info(
            "knowledge source teardown celery finished ks_id=%s status=%s",
            knowledge_source_id,
            result.get("status"),
        )
        return result


def due_knowledge_source_teardown_ids(
    *,
    limit: int,
    now: datetime | None = None,
) -> list[int]:
    """Return due rows with no live worker lease."""

    from apps.lens_bridge.models import LensKnowledgeSource

    now = now or timezone.now()
    stale_claim = now - timedelta(seconds=TEARDOWN_CLAIM_TTL_SECONDS)
    return list(
        LensKnowledgeSource.all_objects.filter(
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.DELETING,
        )
        .filter(
            Q(
                teardown_state_json__blocking__intervention_required__isnull=True
            )
            | Q(
                teardown_state_json__blocking__intervention_required=False
            )
        )
        .filter(
            Q(teardown_next_retry_at__isnull=True)
            | Q(teardown_next_retry_at__lte=now)
        )
        .filter(
            Q(teardown_claimed_at__isnull=True)
            | Q(teardown_claimed_at__lte=stale_claim)
        )
        .order_by("teardown_next_retry_at", "id")
        .values_list("id", flat=True)[: max(1, min(int(limit), 500))]
    )
