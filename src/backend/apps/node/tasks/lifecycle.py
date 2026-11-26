"""Celery tasks for node lifecycle state advancement (upgrade/remove verify)."""

from __future__ import annotations

import logging
from itertools import chain

from celery import shared_task

from apps.node import conf as node_conf
from apps.node.constants import (
    TASK_ADVANCE_ACTIVE_LIFECYCLE_NODES,
    TASK_ADVANCE_NODE_LIFECYCLE,
)
from apps.node.models import Node, NodeTask
from apps.node.services.internal import redis_store
from apps.node.services.internal.node_lifecycle import advance_node_lifecycle
from apps.node.services.internal.node_upgrade_recovery import (
    reconnect_timeout_recovery_node_ids,
)
from common.observability.celery_context import logged_celery_task

logger = logging.getLogger(__name__)

_LIFECYCLE_TASK_KINDS = ("agent.upgrade", "agent.uninstall")
_ACTIVE_STATUSES = (NodeTask.Status.PENDING, NodeTask.Status.RUNNING)


@shared_task(
    name=TASK_ADVANCE_NODE_LIFECYCLE,
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
@logged_celery_task(name=TASK_ADVANCE_NODE_LIFECYCLE, trace_keys=("node_id",))
def advance_node_lifecycle_for_node(self, *, node_id: int) -> dict[str, int]:
    node = (
        Node.objects.select_related("organization")
        .filter(pk=int(node_id), is_deleted=False)
        .first()
    )
    if node is None:
        return {"node_id": int(node_id), "advanced": 0}
    summary = advance_node_lifecycle(org=node.organization, node=node, user=None)
    if summary:
        logger.info("advance_node_lifecycle node_id=%s summary=%s", node_id, summary)
    return {"node_id": int(node_id), "advanced": 1}


@shared_task(
    name=TASK_ADVANCE_ACTIVE_LIFECYCLE_NODES,
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
@logged_celery_task(name=TASK_ADVANCE_ACTIVE_LIFECYCLE_NODES)
def advance_active_lifecycle_nodes(self) -> dict[str, int]:
    del self
    with redis_store.periodic_lease(
        name="node_lifecycle_advance",
        ttl_seconds=node_conf.LIFECYCLE_ADVANCE_LOCK_SECONDS,
    ) as lease:
        if not lease:
            return {"advanced": 0, "coalesced": 1}

        task_rows = list(
            NodeTask.objects.filter(
                correlation_type=node_conf.LIFECYCLE_CORRELATION_TYPE,
                kind__in=_LIFECYCLE_TASK_KINDS,
                status__in=_ACTIVE_STATUSES,
            )
            .values_list("node_id", flat=True)
            .distinct()
        )
        advanced = 0
        seen: set[int] = set()
        for node_id in chain(task_rows, reconnect_timeout_recovery_node_ids()):
            if node_id in seen:
                continue
            seen.add(node_id)
            if not lease.refresh():
                logger.warning(
                    "advance_active_lifecycle stopped after lease loss advanced=%s",
                    advanced,
                )
                return {"advanced": advanced, "coalesced": 0, "lease_lost": 1}
            node = (
                Node.objects.select_related("organization")
                .filter(pk=node_id, is_deleted=False)
                .first()
            )
            if node is None:
                continue
            summary = advance_node_lifecycle(
                org=node.organization,
                node=node,
                user=None,
            )
            if summary:
                logger.info(
                    "advance_active_lifecycle node_id=%s summary=%s",
                    node_id,
                    summary,
                )
            advanced += 1
        return {"advanced": advanced, "coalesced": 0}
