"""Durable Chat data update queue and lost-worker reconciliation."""

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.lens_bridge.models import LensKnowledgeSource
from apps.lens_bridge.services.chat_data_update import UPDATE_CLAIM_TTL


@shared_task(
    name="apps.lens_bridge.tasks.chat_data_update.execute_chat_data_update_task"
)
def execute_chat_data_update_task(
    *, organization_id: int, knowledge_source_id: int
) -> dict:
    from apps.lens_bridge.services.chat_data_update import run_chat_data_update

    result = run_chat_data_update(
        organization_id=organization_id, knowledge_source_id=knowledge_source_id
    )
    if result["status"] == "waiting":
        execute_chat_data_update_task.apply_async(
            kwargs={
                "organization_id": organization_id,
                "knowledge_source_id": knowledge_source_id,
            },
            countdown=max(1, int(result.get("retry_after_seconds") or 5)),
        )
    return result


@shared_task(
    name="apps.lens_bridge.tasks.chat_data_update.reconcile_chat_data_updates_task"
)
def reconcile_chat_data_updates_task(*, limit: int = 100) -> dict:
    now = timezone.now()
    rows = (
        LensKnowledgeSource.objects.filter(
            lifecycle_status=LensKnowledgeSource.LifecycleStatus.READY,
            sync_state_json__chat_data_update__status__in=["pending", "running"],
        )
        .filter(
            Q(sync_next_poll_at__isnull=True) | Q(sync_next_poll_at__lte=now),
            Q(sync_claimed_at__isnull=True)
            | Q(sync_claimed_at__lte=now - UPDATE_CLAIM_TTL),
        )
        .values_list("organization_id", "id")[: max(1, min(limit, 500))]
    )
    queued = 0
    for organization_id, knowledge_source_id in rows:
        try:
            execute_chat_data_update_task.delay(
                organization_id=organization_id,
                knowledge_source_id=knowledge_source_id,
            )
        except Exception:
            continue
        queued += 1
    return {"queued": queued}
