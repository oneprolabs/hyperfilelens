"""Low-cardinality metrics for Agent WebSocket result delivery."""

from prometheus_client import Counter, Histogram


TASK_RESULT_BYTES = Histogram(
    "hfl_agent_task_result_bytes",
    "Serialized Agent task.result frame size in bytes.",
    buckets=(1024, 4096, 16384, 65536, 131072, 262144, 524288, 1048576),
)
TASK_RESULT_TRUNCATED = Counter(
    "hfl_agent_task_result_truncated_total",
    "Agent task results marked as truncated before delivery.",
)
TASK_RESULT_ACK_LATENCY = Histogram(
    "hfl_agent_task_result_ack_latency_seconds",
    "Time from task.result receipt to durable task.result ACK.",
)
TASK_RESULT_RETRANSMISSIONS = Counter(
    "hfl_agent_task_result_retransmissions_total",
    "Task results received after the NodeTask was already terminal.",
)
AGENT_WS_DISCONNECTS = Counter(
    "hfl_agent_websocket_disconnects_total",
    "Agent WebSocket disconnects by normalized close code.",
    ("code",),
)
AGENT_UPLINK_REJECTED = Counter(
    "hfl_agent_uplink_rejected_total",
    "Agent uplink frames rejected before persistence.",
    ("reason",),
)
