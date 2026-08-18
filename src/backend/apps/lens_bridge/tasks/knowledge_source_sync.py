from __future__ import annotations

import logging
from datetime import datetime, timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from common.observability.celery_context import celery_trace

logger = logging.getLogger(__name__)

_SOFT_LIMIT = int(getattr(settings, "LENS_KS_SYNC_SOFT_TIME_LIMIT", 3600))
_TIME_LIMIT = int(getattr(settings, "LENS_KS_SYNC_TIME_LIMIT", 7200))


@shared_task(
    name="apps.lens_bridge.tasks.knowledge_source_sync.execute_knowledge_source_sync_task",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 1},
    soft_time_limit=_SOFT_LIMIT,
    time_limit=_TIME_LIMIT,
)
def execute_knowledge_source_sync_task(
    self,
    *,
    organization_id: int,
    knowledge_source_id: int,
    mode: str = "resume",
) -> dict:
    trace_id = f"ks-sync-{knowledge_source_id}"
    with celery_trace(trace_id, task_name="apps.lens_bridge.tasks.knowledge_source_sync.execute_knowledge_source_sync_task"):
        from apps.lens_bridge.services.knowledge_source_sync import run_knowledge_source_sync

        logger.info(
            "knowledge source sync celery started ks_id=%s org_id=%s mode=%s",
            knowledge_source_id,
            organization_id,
            mode,
        )
        result = run_knowledge_source_sync(
            organization_id=int(organization_id),
            knowledge_source_id=int(knowledge_source_id),
        )
        if result.get("status") == "waiting":
            from apps.lens_bridge.services.managed_datasource import (
                CONVERSION_RETRY_SECONDS,
            )

            self.apply_async(
                kwargs={
                    "organization_id": int(organization_id),
                    "knowledge_source_id": int(knowledge_source_id),
                    "mode": "resume",
                },
                countdown=max(
                    CONVERSION_RETRY_SECONDS,
                    int(result.get("retry_after_seconds") or 0),
                ),
            )
        logger.info(
            "knowledge source sync celery finished ks_id=%s status=%s",
            knowledge_source_id,
            result.get("status"),
        )
        return result


def due_knowledge_source_sync_ids(
    *,
    limit: int,
    now: datetime | None = None,
) -> list[tuple[int, int]]:
    """Return due sync rows whose durable lease is absent or stale."""

    from apps.lens_bridge.models import LensKnowledgeSource
    from apps.lens_bridge.services.knowledge_source_sync import (
        SYNC_CLAIM_TTL_SECONDS,
    )

    now = now or timezone.now()
    stale_claim = now - timedelta(seconds=SYNC_CLAIM_TTL_SECONDS)
    return list(
        LensKnowledgeSource.objects.filter(
            status=LensKnowledgeSource.Status.SYNCING,
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.READY,
        )
        .filter(
            Q(sync_next_poll_at__isnull=True)
            | Q(sync_next_poll_at__lte=now)
        )
        .filter(
            Q(sync_claimed_at__isnull=True)
            | Q(sync_claimed_at__lte=stale_claim)
        )
        .order_by("sync_next_poll_at", "id")
        .values_list("organization_id", "id")[: max(1, min(int(limit), 500))]
    )


@shared_task(
    name=(
        "apps.lens_bridge.tasks.knowledge_source_sync."
        "reconcile_knowledge_source_syncs_task"
    ),
)
def reconcile_knowledge_source_syncs_task(*, limit: int = 100) -> dict:
    """Requeue due Knowledge Source sync work after queue or worker loss."""

    rows = due_knowledge_source_sync_ids(limit=limit)
    queued_ids: list[int] = []
    failures: list[dict[str, str | int]] = []
    for organization_id, knowledge_source_id in rows:
        try:
            execute_knowledge_source_sync_task.delay(
                organization_id=organization_id,
                knowledge_source_id=knowledge_source_id,
                mode="resume",
            )
            queued_ids.append(knowledge_source_id)
        except Exception as exc:
            logger.exception(
                "knowledge source sync reconcile dispatch failed ks_id=%s",
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
        "queued": len(queued_ids),
        "knowledge_source_ids": queued_ids,
        "failed": failures,
    }
