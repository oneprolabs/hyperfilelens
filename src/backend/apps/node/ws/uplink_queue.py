"""
Redis stream queue for Agent WebSocket uplink follow-up (heartbeat inventory, task frames).

The Daphne hot path only touches ``agent_loc`` and enqueues payloads here; Celery
workers drain the stream and persist to PostgreSQL.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from typing import Any

from redis.exceptions import RedisError, ResponseError

from apps.node import conf as node_conf
from apps.node.services.internal import redis_store
from apps.node.ws.wire import ParsedUplink, WireType

logger = logging.getLogger(__name__)

NODE_UPLINK_STREAM = "node:uplink:stream"
NODE_UPLINK_DEAD_LETTER_STREAM = "node:uplink:dead-letter"
NODE_UPLINK_RECLAIM_CURSOR = "node:uplink:reclaim-cursor"
UPLINK_INGEST_GROUP = "node-uplink-ingest"
UPLINK_INGEST_CONSUMER = f"{os.getenv('HOSTNAME', 'worker')}:{os.getpid()}"


def _redis():
    return redis_store.get_redis()


def ensure_uplink_stream_group(client=None) -> None:
    r = client if client is not None else _redis()
    if r is None:
        return
    try:
        r.xgroup_create(
            NODE_UPLINK_STREAM,
            UPLINK_INGEST_GROUP,
            id="0",
            mkstream=True,
        )
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def touch_agent_session_fast(*, node_id: int, session_id: str) -> bool | None:
    """Refresh the authenticated WebSocket route without touching PostgreSQL.

    Heartbeats are the usual caller, but task frames are also received on the
    same live socket.  Refreshing the lease for every authenticated frame
    prevents a route key that expired during a long heartbeat gap from making
    an otherwise healthy task result look stale.
    """
    route_current = redis_store.ensure_agent_location_on_heartbeat(
        agent_id=node_id,
        session_id=session_id,
    )
    redis_store.touch_ws_instance_alive()
    return route_current


def touch_heartbeat_fast(*, node_id: int, session_id: str) -> bool | None:
    """Backward-compatible heartbeat wrapper for the hot-path helper."""
    return touch_agent_session_fast(node_id=node_id, session_id=session_id)


def _serialize_uplink(
    *,
    node_id: int,
    message: ParsedUplink,
    session_id: str | None = None,
    marker_token: str = "",
) -> dict[str, str]:
    payload: dict[str, Any] = {
        "node_id": node_id,
        "msg_type": str(message.msg_type),
    }
    if session_id:
        payload["session_id"] = str(session_id)
    if message.msg_type == WireType.HEARTBEAT:
        payload["heartbeat_payload"] = message.heartbeat_payload
    else:
        payload["marker_token"] = marker_token
        payload["task_id"] = message.task_id
        payload["progress"] = message.progress
        payload["is_alive"] = message.is_alive
        payload["status"] = message.status
        payload["result"] = message.result
        payload["error"] = message.error
    return {"payload": json.dumps(payload, ensure_ascii=False)}


def enqueue_uplink(
    *, node_id: int, message: ParsedUplink, session_id: str | None = None
) -> None:
    r = _redis()
    if r is None:
        from apps.node.tasks.uplink_ingest import process_uplink_payload

        process_uplink_payload.delay(
            payload=_serialize_uplink(
                node_id=node_id,
                message=message,
                session_id=session_id,
            )["payload"]
        )
        return
    ensure_uplink_stream_group(r)
    if message.task_id:
        marker_token = uuid.uuid4().hex
        fields = _serialize_uplink(
            node_id=node_id,
            message=message,
            session_id=session_id,
            marker_token=marker_token,
        )
        redis_store.enqueue_uplink_with_activity(
            r,
            stream_name=NODE_UPLINK_STREAM,
            fields=fields,
            task_id=message.task_id,
            message_type=str(message.msg_type),
            marker_token=marker_token,
        )
        return
    r.xadd(
        NODE_UPLINK_STREAM,
        _serialize_uplink(node_id=node_id, message=message, session_id=session_id),
    )


def _deserialize_payload(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def payload_to_parsed(
    data: dict[str, Any],
) -> tuple[int, ParsedUplink, str | None] | None:
    node_id_raw = data.get("node_id")
    if not isinstance(node_id_raw, int):
        try:
            node_id = int(node_id_raw)
        except (TypeError, ValueError):
            return None
    else:
        node_id = node_id_raw

    msg_type_raw = str(data.get("msg_type", "")).strip().lower()
    try:
        msg_type = WireType(msg_type_raw)
    except ValueError:
        return None

    if msg_type == WireType.HEARTBEAT:
        hb = data.get("heartbeat_payload")
        heartbeat_payload = hb if isinstance(hb, dict) else None
        return (
            node_id,
            ParsedUplink(msg_type=msg_type, heartbeat_payload=heartbeat_payload),
            str(data.get("session_id") or "") or None,
        )

    task_id = data.get("task_id")
    if not task_id:
        return None
    return (
        node_id,
        ParsedUplink(
            msg_type=msg_type,
            task_id=str(task_id),
            progress=data.get("progress")
            if isinstance(data.get("progress"), dict)
            else None,
            is_alive=bool(data.get("is_alive")),
            status=str(data.get("status") or "") or None,
            result=data.get("result")
            if isinstance(data.get("result"), dict)
            else None,
            error=str(data.get("error") or ""),
        ),
        str(data.get("session_id") or "") or None,
    )


def _acknowledge_entry(
    r,
    *,
    entry_id: str,
    task_id: str = "",
    marker_token: str = "",
) -> None:
    """Atomically clear this entry's marker and acknowledge its projection."""
    marker_key = (
        redis_store.task_uplink_activity_key(task_id)
        if task_id and marker_token
        else "task_uplink_activity:unused"
    )
    r.eval(
        """
        local acknowledged = redis.call('xack', KEYS[1], ARGV[1], ARGV[2])
        local deleted = redis.call('xdel', KEYS[1], ARGV[2])
        local marker_deleted = 0
        if ARGV[3] ~= '' then
            local raw = redis.call('get', KEYS[2])
            if raw then
                local decoded, payload = pcall(cjson.decode, raw)
                if decoded and type(payload) == 'table'
                    and tostring(payload['marker_token'] or '') == ARGV[3] then
                    marker_deleted = redis.call('del', KEYS[2])
                end
            end
        end
        return {acknowledged, deleted, marker_deleted}
        """,
        2,
        NODE_UPLINK_STREAM,
        marker_key,
        UPLINK_INGEST_GROUP,
        str(entry_id),
        str(marker_token),
    )


def stream_entry_age_seconds(entry_id: str, *, now: float | None = None) -> float:
    """Return stable time since a Redis Stream entry was originally added."""
    try:
        created_at = int(str(entry_id).split("-", 1)[0]) / 1000
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, float(now if now is not None else time.time()) - created_at)


def _pending_delivery_count(r, *, entry_id: str) -> int:
    try:
        rows = r.xpending_range(
            NODE_UPLINK_STREAM,
            UPLINK_INGEST_GROUP,
            min=entry_id,
            max=entry_id,
            count=1,
        )
    except (AttributeError, ResponseError):
        return 0
    if not rows:
        return 0
    try:
        return max(0, int(rows[0].get("times_delivered", 0)))
    except (AttributeError, TypeError, ValueError):
        return 0


def _quarantine_failed_entry(
    r,
    *,
    entry_id: str,
    raw_payload: str,
    error: Exception,
    task_id: str = "",
    marker_token: str = "",
) -> tuple[bool, int, float]:
    """Move a persistently failing entry to DLQ without discarding its payload."""
    deliveries = _pending_delivery_count(r, entry_id=entry_id)
    age_seconds = stream_entry_age_seconds(entry_id)
    if (
        deliveries < node_conf.UPLINK_DLQ_MIN_DELIVERIES
        or age_seconds < node_conf.UPLINK_DLQ_MIN_AGE_SECONDS
    ):
        return False, deliveries, age_seconds
    try:
        marker_key = (
            redis_store.task_uplink_activity_key(task_id)
            if task_id and marker_token
            else "task_uplink_activity:unused"
        )
        r.eval(
            """
            local dead_letter_id = redis.call(
                'xadd', KEYS[2], '*',
                'source_entry_id', ARGV[3],
                'payload', ARGV[4],
                'deliveries', ARGV[5],
                'entry_age_seconds', ARGV[6],
                'error_type', ARGV[7],
                'quarantined_at', ARGV[8]
            )
            redis.call('xack', KEYS[1], ARGV[1], ARGV[2])
            redis.call('xdel', KEYS[1], ARGV[2])
            if ARGV[9] ~= '' then
                local raw = redis.call('get', KEYS[3])
                if raw then
                    local decoded, payload = pcall(cjson.decode, raw)
                    if decoded and type(payload) == 'table'
                        and tostring(payload['marker_token'] or '') == ARGV[9] then
                        redis.call('del', KEYS[3])
                    end
                end
            end
            return dead_letter_id
            """,
            3,
            NODE_UPLINK_STREAM,
            NODE_UPLINK_DEAD_LETTER_STREAM,
            marker_key,
            UPLINK_INGEST_GROUP,
            str(entry_id),
            str(entry_id),
            str(raw_payload),
            str(deliveries),
            str(int(age_seconds)),
            type(error).__name__,
            str(time.time()),
            str(marker_token),
        )
    except RedisError:
        logger.exception("uplink DLQ transaction failed entry_id=%s", entry_id)
        return False, deliveries, age_seconds
    return True, deliveries, age_seconds


def replay_dead_letter_entry(r, *, entry_id: str, fields: dict[str, str]) -> str:
    """Atomically restore one DLQ payload to the live uplink stream."""
    raw_payload = fields.get("payload") if isinstance(fields, dict) else None
    if not raw_payload:
        raise ValueError("dead-letter entry has no payload")
    data = _deserialize_payload(str(raw_payload))
    parsed = payload_to_parsed(data) if data is not None else None
    if parsed is None:
        raise ValueError("dead-letter payload is not a valid Agent uplink")
    _node_id, message, _session_id = parsed
    ensure_uplink_stream_group(r)
    if message.task_id:
        marker_token = uuid.uuid4().hex
        data["marker_token"] = marker_token
        payload = json.dumps(data, ensure_ascii=False)
        marker_payload, marker_ttl = redis_store.task_uplink_activity_record(
            message_type=str(message.msg_type),
            marker_token=marker_token,
        )
        result = r.eval(
            """
            local live_entry_id = redis.call(
                'xadd', KEYS[1], '*', 'payload', ARGV[1]
            )
            redis.call('set', KEYS[3], ARGV[2], 'EX', ARGV[3])
            redis.call('xdel', KEYS[2], ARGV[4])
            return live_entry_id
            """,
            3,
            NODE_UPLINK_STREAM,
            NODE_UPLINK_DEAD_LETTER_STREAM,
            redis_store.task_uplink_activity_key(message.task_id),
            payload,
            marker_payload,
            marker_ttl,
            entry_id,
        )
    else:
        result = r.eval(
            """
            local live_entry_id = redis.call(
                'xadd', KEYS[1], '*', 'payload', ARGV[1]
            )
            redis.call('xdel', KEYS[2], ARGV[2])
            return live_entry_id
            """,
            2,
            NODE_UPLINK_STREAM,
            NODE_UPLINK_DEAD_LETTER_STREAM,
            str(raw_payload),
            entry_id,
        )
    return str(result)


def _claim_stale_entries(r, *, count: int) -> list[tuple[str, dict[str, str]]]:
    """Claim abandoned pending messages left by a failed Worker process."""
    start_id = str(r.get(NODE_UPLINK_RECLAIM_CURSOR) or "0-0")
    try:
        result = r.xautoclaim(
            NODE_UPLINK_STREAM,
            UPLINK_INGEST_GROUP,
            UPLINK_INGEST_CONSUMER,
            min_idle_time=node_conf.UPLINK_PENDING_RECLAIM_IDLE_MS,
            start_id=start_id,
            count=count,
        )
    except (AttributeError, ResponseError):
        return []
    if not isinstance(result, (list, tuple)) or len(result) < 2:
        return []
    next_start_id = str(result[0] or "0-0")
    try:
        r.set(NODE_UPLINK_RECLAIM_CURSOR, next_start_id)
    except RedisError as exc:
        logger.warning("uplink reclaim cursor persist failed: %s", exc)
    messages = result[1]
    return list(messages) if isinstance(messages, (list, tuple)) else []


def _read_new_entries(r, *, count: int) -> list[tuple[str, dict[str, str]]]:
    try:
        rows = r.xreadgroup(
            UPLINK_INGEST_GROUP,
            UPLINK_INGEST_CONSUMER,
            {NODE_UPLINK_STREAM: ">"},
            count=count,
            block=1,
        )
    except ResponseError:
        ensure_uplink_stream_group(r)
        rows = r.xreadgroup(
            UPLINK_INGEST_GROUP,
            UPLINK_INGEST_CONSUMER,
            {NODE_UPLINK_STREAM: ">"},
            count=count,
            block=1,
        )
    if not rows:
        return []
    return list(rows[0][1])


def drain_uplink_stream(*, count: int | None = None) -> int:
    """
    Consume up to ``count`` uplink entries from Redis and return processed count.

    Falls back to zero when Redis is unavailable.
    """
    r = _redis()
    if r is None:
        return 0

    batch = max(1, int(count or node_conf.UPLINK_INGEST_BATCH_SIZE))
    ensure_uplink_stream_group(r)

    # Reserve most of every batch for fresh uplink so a set of poison pending
    # entries cannot indefinitely starve newly arriving Agent messages.
    reclaim_limit = 1 if batch == 1 else max(1, min(batch - 1, batch // 4))
    messages = _claim_stale_entries(r, count=reclaim_limit)
    remaining = batch - len(messages)
    if remaining > 0:
        messages.extend(_read_new_entries(r, count=remaining))
    if not messages:
        return 0

    from apps.node.ws.uplink import handle_uplink

    processed = 0
    for entry_id, fields in messages:
        raw = fields.get("payload") if isinstance(fields, dict) else None
        if not raw:
            _acknowledge_entry(r, entry_id=entry_id)
            continue
        data = _deserialize_payload(str(raw))
        if data is None:
            _acknowledge_entry(r, entry_id=entry_id)
            continue
        parsed = payload_to_parsed(data)
        if parsed is None:
            _acknowledge_entry(r, entry_id=entry_id)
            continue
        node_id, message, session_id = parsed
        marker_token = str(data.get("marker_token") or "")
        try:
            handle_uplink(
                node_id=node_id,
                message=message,
                session_id=session_id,
            )
        except Exception as exc:
            quarantined, deliveries, age_seconds = _quarantine_failed_entry(
                r,
                entry_id=entry_id,
                raw_payload=str(raw),
                error=exc,
                task_id=str(message.task_id or ""),
                marker_token=marker_token,
            )
            if quarantined:
                logger.error(
                    "uplink quarantined after projection failures "
                    "entry_id=%s node_id=%s msg_type=%s deliveries=%s age_seconds=%s",
                    entry_id,
                    node_id,
                    message.msg_type,
                    deliveries,
                    int(age_seconds),
                )
            elif deliveries <= 3 or deliveries in {5, 10, 25, 50}:
                logger.exception(
                    "uplink ingest failed; entry remains pending "
                    "node_id=%s msg_type=%s deliveries=%s",
                    node_id,
                    message.msg_type,
                    deliveries,
                )
            continue
        _acknowledge_entry(
            r,
            entry_id=entry_id,
            task_id=str(message.task_id or ""),
            marker_token=marker_token,
        )
        processed += 1
    return processed
