"""Durable and periodic repair for the source backup Pipeline read model."""

import logging

from celery import shared_task
from django.db import OperationalError

from apps.source.services.internal.source_pipeline import (
    _is_retryable_projection_error,
    reconcile_pipeline_projections,
    sync_pipeline_projection_with_retry,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    name="apps.source.tasks.pipeline.sync_source_pipeline_projection_task",
)
def sync_source_pipeline_projection_task(
    self,
    *,
    organization_id: int,
    source_kind: str,
    ref_id: int,
) -> dict[str, object]:
    """Refresh one source projection after its authoritative state commits."""
    try:
        entry = sync_pipeline_projection_with_retry(
            organization_id=int(organization_id),
            source_kind=str(source_kind),
            ref_id=int(ref_id),
            max_retries=1,
        )
    except OperationalError as exc:
        if not _is_retryable_projection_error(exc):
            logger.exception(
                "source pipeline projection failed with non-retryable database error "
                "organization_id=%s source_kind=%s ref_id=%s",
                organization_id,
                source_kind,
                ref_id,
            )
            return {"status": "failed", "reason": "projection_error"}
        retries = int(getattr(self.request, "retries", 0) or 0)
        if retries < self.max_retries:
            raise self.retry(exc=exc, countdown=2 ** retries)
        logger.exception(
            "source pipeline projection exhausted retries organization_id=%s "
            "source_kind=%s ref_id=%s",
            organization_id,
            source_kind,
            ref_id,
        )
        return {"status": "failed", "reason": "projection_error"}
    except Exception:
        logger.exception(
            "source pipeline projection failed organization_id=%s source_kind=%s "
            "ref_id=%s",
            organization_id,
            source_kind,
            ref_id,
        )
        return {"status": "failed", "reason": "projection_error"}
    return {"status": "updated" if entry is not None else "skipped"}


def queue_source_pipeline_projection(
    *, organization_id: int, source_kind: str, ref_id: int
) -> bool:
    """Queue a projection without turning a committed source result into an API error."""
    try:
        sync_source_pipeline_projection_task.apply_async(
            kwargs={
                "organization_id": int(organization_id),
                "source_kind": str(source_kind),
                "ref_id": int(ref_id),
            }
        )
    except Exception:
        logger.exception(
            "source pipeline projection enqueue failed organization_id=%s "
            "source_kind=%s ref_id=%s",
            organization_id,
            source_kind,
            ref_id,
        )
        return False
    return True


@shared_task(name="apps.source.tasks.pipeline.reconcile_source_pipeline_task")
def reconcile_source_pipeline_task(*, limit: int = 100) -> dict[str, int]:
    return reconcile_pipeline_projections(limit=max(1, int(limit)))
