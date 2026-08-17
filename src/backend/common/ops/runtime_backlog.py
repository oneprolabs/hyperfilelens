"""Read-only Redis backlog snapshot shared by metrics and Platform Ops."""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any

from django.conf import settings
from redis.exceptions import RedisError, ResponseError

from apps.node.services.internal import redis_store
from apps.node.ws.uplink_queue import (
    NODE_UPLINK_DEAD_LETTER_STREAM,
    NODE_UPLINK_STREAM,
    UPLINK_INGEST_GROUP,
    stream_entry_age_seconds,
)


QUEUE_DEPTH_WARNING = 500
UPLINK_PENDING_AGE_WARNING_SECONDS = 60
UPLINK_LAG_WARNING = 500
UPLINK_ACKNOWLEDGED_HISTORY_WARNING = 500
REDIS_MEMORY_RATIO_WARNING = 0.8
TASK_STREAM_STATS_CACHE_SECONDS = 60

_TASK_STREAM_STATS_LOCK = threading.Lock()
_task_stream_stats_cache: tuple[int, float, int, int] = (0, 0.0, 0, 0)


def _empty_snapshot(*, status: str, error: str, warning: str) -> dict[str, Any]:
    return {
        "status": status,
        "error": error,
        "queue_depths": {},
        "redis_used_memory_bytes": 0,
        "redis_memory_limit_bytes": 0,
        "redis_memory_ratio": 0.0,
        "uplink_stream_length": 0,
        "uplink_acknowledged_history": 0,
        "uplink_lag": 0,
        "uplink_pending": 0,
        "uplink_dead_letter": 0,
        "uplink_oldest_pending_seconds": 0.0,
        "uplink_oldest_unread_seconds": 0.0,
        "task_stream_keys": 0,
        "task_stream_keys_without_ttl": 0,
        "warnings": [warning],
    }


def _parse_memory_bytes(value: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*([kmgt]?)b?\s*", value.lower())
    if not match:
        return 0
    amount = int(match.group(1))
    power = {"": 0, "k": 1, "m": 2, "g": 3, "t": 4}[match.group(2)]
    return amount * (1024**power)


def _redis_memory_snapshot(client) -> tuple[int, int, float]:
    try:
        info = client.info("memory")
        used = int((info or {}).get("used_memory", 0))
        redis_limit = int((info or {}).get("maxmemory", 0))
    except (RedisError, AttributeError, TypeError, ValueError):
        return 0, 0, 0.0
    configured_limit = _parse_memory_bytes(os.getenv("HFL_REDIS_MEMORY_LIMIT", ""))
    limit = redis_limit or configured_limit
    return used, limit, (used / limit if limit > 0 else 0.0)


def _task_stream_stats(client) -> tuple[int, int]:
    """Return short-lived List counts without running KEYS on a hot Redis."""
    global _task_stream_stats_cache
    now = time.monotonic()
    client_id, collected_at, total, without_ttl = _task_stream_stats_cache
    if client_id == id(client) and now - collected_at < TASK_STREAM_STATS_CACHE_SECONDS:
        return total, without_ttl
    with _TASK_STREAM_STATS_LOCK:
        client_id, collected_at, total, without_ttl = _task_stream_stats_cache
        if (
            client_id == id(client)
            and now - collected_at < TASK_STREAM_STATS_CACHE_SECONDS
        ):
            return total, without_ttl
        total = 0
        without_ttl = 0

        def inspect(batch: list[str]) -> None:
            nonlocal total, without_ttl
            if not batch:
                return
            pipeline = client.pipeline(transaction=False)
            for key in batch:
                pipeline.ttl(key)
            total += len(batch)
            without_ttl += sum(1 for ttl in pipeline.execute() if int(ttl) == -1)

        batch: list[str] = []
        for key in client.scan_iter(match="task_stream:*", count=200):
            batch.append(str(key))
            if len(batch) == 200:
                inspect(batch)
                batch = []
        inspect(batch)
        _task_stream_stats_cache = (id(client), now, total, without_ttl)
        return total, without_ttl


def _uplink_group_lag(client, *, stream_length: int) -> tuple[int, float]:
    try:
        groups = client.xinfo_groups(NODE_UPLINK_STREAM)
    except ResponseError as exc:
        if "NO SUCH KEY" not in str(exc).upper():
            raise
        groups = []
    group = next(
        (
            item
            for item in groups or []
            if str(item.get("name") or "") == UPLINK_INGEST_GROUP
        ),
        None,
    )
    if group is None:
        lag = stream_length
        last_delivered_id = "0-0"
    else:
        raw_lag = group.get("lag")
        if raw_lag is None:
            entries_read = group.get("entries-read")
            lag = (
                stream_length
                if entries_read is None
                else max(0, stream_length - int(entries_read))
            )
        else:
            lag = max(0, int(raw_lag))
        last_delivered_id = str(group.get("last-delivered-id") or "0-0")
    oldest_unread_seconds = 0.0
    if lag:
        rows = client.xrange(
            NODE_UPLINK_STREAM,
            min=f"({last_delivered_id}",
            max="+",
            count=1,
        )
        if rows:
            oldest_unread_seconds = stream_entry_age_seconds(str(rows[0][0]))
    return lag, oldest_unread_seconds


def _queue_names() -> list[str]:
    queues = getattr(settings, "CELERY_TASK_QUEUES", ())
    names = {str(getattr(queue, "name", queue)) for queue in queues}
    return sorted(name for name in names if name)


def runtime_backlog_snapshot() -> dict[str, Any]:
    """Return queue depths and Agent uplink lag without raising probe errors."""
    client = redis_store.get_redis()
    if client is None:
        return _empty_snapshot(
            status="error",
            error="Redis is unavailable.",
            warning="Redis backlog metrics are unavailable.",
        )
    try:
        queue_depths = {
            queue_name: int(client.llen(queue_name)) for queue_name in _queue_names()
        }
        stream_length = int(client.xlen(NODE_UPLINK_STREAM))
        dead_letter_count = int(client.xlen(NODE_UPLINK_DEAD_LETTER_STREAM))
        uplink_lag, oldest_unread_seconds = _uplink_group_lag(
            client,
            stream_length=stream_length,
        )
        try:
            pending = client.xpending(NODE_UPLINK_STREAM, UPLINK_INGEST_GROUP)
        except ResponseError as exc:
            if "NOGROUP" not in str(exc).upper():
                raise
            pending = {"pending": 0}
        pending_count = int((pending or {}).get("pending", 0))
        oldest_seconds = 0.0
        if pending_count:
            oldest_seconds = stream_entry_age_seconds(
                str((pending or {}).get("min") or "")
            )
        used_memory, memory_limit, memory_ratio = _redis_memory_snapshot(client)
        task_stream_keys, task_stream_without_ttl = _task_stream_stats(client)
    except (RedisError, TypeError, ValueError) as exc:
        return _empty_snapshot(
            status="error",
            error=type(exc).__name__,
            warning="Redis backlog metrics could not be collected.",
        )

    warnings = [
        f"Celery queue {name} depth is {depth}."
        for name, depth in queue_depths.items()
        if depth >= QUEUE_DEPTH_WARNING
    ]
    if oldest_seconds >= UPLINK_PENDING_AGE_WARNING_SECONDS:
        warnings.append(
            f"Agent uplink projection is {int(oldest_seconds)} seconds behind."
        )
    acknowledged_history = max(
        0,
        stream_length - pending_count - uplink_lag,
    )
    if acknowledged_history >= UPLINK_ACKNOWLEDGED_HISTORY_WARNING:
        warnings.append(
            "Agent uplink retains "
            f"{acknowledged_history} already-acknowledged historical entries."
        )
    if (
        uplink_lag >= UPLINK_LAG_WARNING
        or oldest_unread_seconds >= UPLINK_PENDING_AGE_WARNING_SECONDS
    ):
        warnings.append(
            "Agent uplink has "
            f"{uplink_lag} unread entries; oldest is "
            f"{int(oldest_unread_seconds)} seconds old."
        )
    if memory_ratio >= REDIS_MEMORY_RATIO_WARNING:
        warnings.append(
            f"Redis memory usage is {memory_ratio * 100:.1f}% of its configured limit."
        )
    if task_stream_without_ttl:
        warnings.append(
            f"Redis contains {task_stream_without_ttl} task notification Lists without TTL."
        )
    if dead_letter_count:
        warnings.append(
            f"Agent uplink dead-letter stream contains {dead_letter_count} entries."
        )
    return {
        "status": "degraded" if warnings else "ok",
        "error": None,
        "queue_depths": queue_depths,
        "redis_used_memory_bytes": used_memory,
        "redis_memory_limit_bytes": memory_limit,
        "redis_memory_ratio": memory_ratio,
        "uplink_stream_length": stream_length,
        "uplink_acknowledged_history": acknowledged_history,
        "uplink_lag": uplink_lag,
        "uplink_pending": pending_count,
        "uplink_dead_letter": dead_letter_count,
        "uplink_oldest_pending_seconds": oldest_seconds,
        "uplink_oldest_unread_seconds": oldest_unread_seconds,
        "task_stream_keys": task_stream_keys,
        "task_stream_keys_without_ttl": task_stream_without_ttl,
        "warnings": warnings,
    }
