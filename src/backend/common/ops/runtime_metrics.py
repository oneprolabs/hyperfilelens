"""Best-effort Redis/Celery runtime metrics for operational alerting."""

from __future__ import annotations

from prometheus_client import Gauge

from common.ops.runtime_backlog import runtime_backlog_snapshot


QUEUE_DEPTH = Gauge(
    "hfl_celery_queue_depth",
    "Number of messages waiting in a Celery Redis queue.",
    ("queue",),
)
UPLINK_STREAM_LENGTH = Gauge(
    "hfl_node_uplink_stream_length",
    "Number of entries retained in the Agent uplink Redis stream.",
)
UPLINK_ACKNOWLEDGED_HISTORY = Gauge(
    "hfl_node_uplink_acknowledged_history",
    "Approximate Agent uplink entries already acknowledged but still retained.",
)
UPLINK_PENDING = Gauge(
    "hfl_node_uplink_pending",
    "Number of Agent uplink entries pending acknowledgement.",
)
UPLINK_LAG = Gauge(
    "hfl_node_uplink_lag",
    "Number of Agent uplink entries not yet delivered to the ingest group.",
)
UPLINK_DEAD_LETTER = Gauge(
    "hfl_node_uplink_dead_letter",
    "Number of Agent uplink entries quarantined for manual replay.",
)
UPLINK_OLDEST_PENDING_SECONDS = Gauge(
    "hfl_node_uplink_oldest_pending_seconds",
    "Age of the oldest pending Agent uplink projection.",
)
UPLINK_OLDEST_UNREAD_SECONDS = Gauge(
    "hfl_node_uplink_oldest_unread_seconds",
    "Age of the oldest Agent uplink entry not yet delivered to ingest.",
)
REDIS_USED_MEMORY_BYTES = Gauge(
    "hfl_redis_used_memory_bytes",
    "Redis memory currently used in bytes.",
)
REDIS_MEMORY_LIMIT_BYTES = Gauge(
    "hfl_redis_memory_limit_bytes",
    "Redis server or configured container memory limit in bytes.",
)
REDIS_MEMORY_RATIO = Gauge(
    "hfl_redis_memory_ratio",
    "Redis used memory divided by its available configured limit.",
)
TASK_STREAM_KEYS = Gauge(
    "hfl_task_stream_keys",
    "Number of short-lived synchronous Agent task notification Lists.",
)
TASK_STREAM_KEYS_WITHOUT_TTL = Gauge(
    "hfl_task_stream_keys_without_ttl",
    "Number of Agent task notification Lists that have no expiry.",
)
RUNTIME_METRICS_COLLECTION_SUCCESS = Gauge(
    "hfl_runtime_metrics_collection_success",
    "Whether the last Redis runtime metrics collection succeeded.",
)


def collect_runtime_metrics() -> None:
    """Refresh queue and uplink gauges without failing the metrics endpoint."""
    snapshot = runtime_backlog_snapshot()
    if snapshot["status"] == "error":
        RUNTIME_METRICS_COLLECTION_SUCCESS.set(0)
        return
    for queue_name, depth in snapshot["queue_depths"].items():
        QUEUE_DEPTH.labels(queue=queue_name).set(depth)
    UPLINK_STREAM_LENGTH.set(snapshot["uplink_stream_length"])
    UPLINK_ACKNOWLEDGED_HISTORY.set(snapshot["uplink_acknowledged_history"])
    UPLINK_LAG.set(snapshot["uplink_lag"])
    UPLINK_PENDING.set(snapshot["uplink_pending"])
    UPLINK_DEAD_LETTER.set(snapshot["uplink_dead_letter"])
    UPLINK_OLDEST_PENDING_SECONDS.set(snapshot["uplink_oldest_pending_seconds"])
    UPLINK_OLDEST_UNREAD_SECONDS.set(snapshot["uplink_oldest_unread_seconds"])
    REDIS_USED_MEMORY_BYTES.set(snapshot["redis_used_memory_bytes"])
    REDIS_MEMORY_LIMIT_BYTES.set(snapshot["redis_memory_limit_bytes"])
    REDIS_MEMORY_RATIO.set(snapshot["redis_memory_ratio"])
    TASK_STREAM_KEYS.set(snapshot["task_stream_keys"])
    TASK_STREAM_KEYS_WITHOUT_TTL.set(snapshot["task_stream_keys_without_ttl"])
    RUNTIME_METRICS_COLLECTION_SUCCESS.set(1)
