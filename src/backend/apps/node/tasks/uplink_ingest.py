"""Celery tasks that drain Agent uplink Redis streams into PostgreSQL."""

from __future__ import annotations

import json
import logging
import time

from celery import shared_task

from apps.node.constants import TASK_INGEST_NODE_UPLINK_STREAMS
from apps.node import conf as node_conf
from apps.node.services.internal import redis_store
from apps.node.ws.uplink import handle_uplink
from apps.node.ws.uplink_queue import (
    UPLINK_INGEST_CONSUMER,
    UPLINK_INGEST_GROUP,
    drain_uplink_stream,
    payload_to_parsed,
)
from common.observability.celery_context import logged_celery_task

logger = logging.getLogger(__name__)


@shared_task(
    name=TASK_INGEST_NODE_UPLINK_STREAMS,
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
@logged_celery_task(name=TASK_INGEST_NODE_UPLINK_STREAMS)
def ingest_node_uplink_streams(self) -> dict[str, int]:
    del self
    with redis_store.periodic_lease(
        name="node_uplink_ingest",
        ttl_seconds=node_conf.UPLINK_INGEST_LOCK_SECONDS,
    ) as lease:
        if not lease:
            return {"processed": 0, "coalesced": 1}

        processed = 0
        batches = 0
        deadline = time.monotonic() + node_conf.UPLINK_INGEST_DRAIN_SECONDS
        while time.monotonic() < deadline:
            if not lease:
                logger.warning(
                    "ingest_node_uplink_streams stopped after lease loss processed=%s",
                    processed,
                )
                return {
                    "processed": processed,
                    "coalesced": 0,
                    "lease_lost": 1,
                }
            batch_processed = drain_uplink_stream()
            processed += batch_processed
            batches += 1
            if batch_processed < node_conf.UPLINK_INGEST_BATCH_SIZE:
                break
        if processed:
            logger.debug(
                "ingest_node_uplink_streams processed=%s batches=%s",
                processed,
                batches,
            )
        return {"processed": processed, "coalesced": 0}


@shared_task(
    name="apps.node.tasks.uplink_ingest.process_uplink_payload",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
@logged_celery_task(name="apps.node.tasks.uplink_ingest.process_uplink_payload")
def process_uplink_payload(self, *, payload: str) -> dict[str, str]:
    """Fallback when Redis streams are unavailable."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"status": "ignored", "reason": "invalid_json"}
    if not isinstance(data, dict):
        return {"status": "ignored", "reason": "invalid_payload"}
    parsed = payload_to_parsed(data)
    if parsed is None:
        return {"status": "ignored", "reason": "unparseable"}
    node_id, message, session_id = parsed
    handle_uplink(node_id=node_id, message=message, session_id=session_id)
    return {"status": "ok", "node_id": str(node_id), "msg_type": str(message.msg_type)}


__all__ = [
    "UPLINK_INGEST_CONSUMER",
    "UPLINK_INGEST_GROUP",
    "ingest_node_uplink_streams",
    "process_uplink_payload",
]
