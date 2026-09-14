"""Celery recovery for durable SourceLens LensNode provisioning."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from common.observability.celery_context import celery_trace

logger = logging.getLogger(__name__)


@shared_task(
    name=(
        "apps.lens_bridge.tasks.gateway_provisioning."
        "execute_gateway_lensnode_provision_task"
    ),
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 1},
    soft_time_limit=240,
    time_limit=300,
)
def execute_gateway_lensnode_provision_task(*, gateway_link_id: int) -> dict:
    """Resume one missing LensNode from its persisted lookup identity."""

    from apps.lens_bridge.models import LensGatewayLink
    from apps.lens_bridge.services import provisioning

    link = (
        LensGatewayLink.objects.select_related(
            "organization",
            "gateway",
            "created_by",
            "owner_user",
        )
        .filter(pk=gateway_link_id, is_deleted=False)
        .first()
    )
    if link is None:
        return {"gateway_link_id": gateway_link_id, "status": "missing"}
    if link.sl_lensnode_uuid:
        return {"gateway_link_id": gateway_link_id, "status": "ready"}
    with celery_trace(
        f"gateway-lensnode-provision-{gateway_link_id}",
        task_name=(
            "apps.lens_bridge.tasks.gateway_provisioning."
            "execute_gateway_lensnode_provision_task"
        ),
    ):
        result = provisioning.ensure_lensnode_for_gateway(
            org=link.organization,
            gateway=link.gateway,
            created_by=link.created_by or link.owner_user,
            scope=link.scope,
        )
    return {
        "gateway_link_id": gateway_link_id,
        "status": "ready",
        "sl_lensnode_uuid": str(result.sl_lensnode_uuid),
    }


@shared_task(
    name=(
        "apps.lens_bridge.tasks.gateway_provisioning."
        "reconcile_gateway_lensnode_provisions_task"
    ),
)
def reconcile_gateway_lensnode_provisions_task(*, limit: int = 100) -> dict:
    """Requeue missing LensNodes whose durable lease is absent or stale."""

    from apps.lens_bridge.models import LensGatewayLink
    from apps.lens_bridge.services.provisioning import (
        LENSNODE_PROVISION_CLAIM_TTL_SECONDS,
    )

    stale_claim = timezone.now() - timedelta(
        seconds=LENSNODE_PROVISION_CLAIM_TTL_SECONDS
    )
    link_ids = list(
        LensGatewayLink.objects.filter(
            is_deleted=False,
            sl_lensnode_uuid__isnull=True,
        )
        .filter(
            Q(lensnode_provision_claimed_at__isnull=True)
            | Q(lensnode_provision_claimed_at__lte=stale_claim)
        )
        .order_by("lensnode_provision_claimed_at", "id")
        .values_list("id", flat=True)[: max(1, min(int(limit), 500))]
    )
    queued: list[int] = []
    failures: list[dict[str, int | str]] = []
    for link_id in link_ids:
        try:
            execute_gateway_lensnode_provision_task.delay(
                gateway_link_id=link_id
            )
            queued.append(link_id)
        except Exception as exc:
            logger.exception(
                "gateway LensNode reconcile dispatch failed link_id=%s",
                link_id,
            )
            failures.append({"id": link_id, "error": str(exc)[:1000]})
    return {"queued_gateway_link_ids": queued, "failures": failures}
