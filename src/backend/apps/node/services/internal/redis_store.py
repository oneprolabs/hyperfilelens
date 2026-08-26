"""
Redis helpers for node communication (agent_loc, task_stream, task_info).
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import redis
from redis.exceptions import TimeoutError as RedisTimeoutError
from django.conf import settings

from apps.node import conf as node_conf

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


class _DefaultRedisClient:
    """Sentinel requesting the lazily initialized process Redis client."""


_DEFAULT_REDIS_CLIENT = _DefaultRedisClient()


def _resolve_redis_client(
    client: redis.Redis | None | _DefaultRedisClient,
) -> redis.Redis | None:
    if isinstance(client, _DefaultRedisClient):
        return get_redis()
    return client


def _broker_url() -> str:
    return getattr(settings, "CELERY_BROKER_URL", "redis://redis:6379/0")


def get_redis() -> redis.Redis | None:
    global _client
    if _client is not None:
        return _client
    try:
        client = redis.Redis.from_url(
            _broker_url(),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=None,
        )
        client.ping()
        _client = client
        return client
    except Exception as exc:
        logger.warning("node redis unavailable: %s", exc)
        return None


def agent_loc_key(agent_id: int) -> str:
    return f"agent_loc:{agent_id}"


def _encode_agent_loc(*, ws_instance_id: str, session_id: str) -> str:
    return json.dumps(
        {"ws": ws_instance_id, "session": session_id},
        ensure_ascii=False,
    )


def _decode_agent_loc(raw: str) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    if not isinstance(payload, dict):
        return raw, None
    ws = payload.get("ws")
    session = payload.get("session")
    ws_text = str(ws).strip() if ws else None
    session_text = str(session).strip() if session else None
    return ws_text or None, session_text or None


def set_agent_location(
    *,
    agent_id: int,
    ws_instance_id: str | None = None,
    session_id: str | None = None,
    redis_client: redis.Redis | None | _DefaultRedisClient = _DEFAULT_REDIS_CLIENT,
) -> bool:
    """Record the current WebSocket route and report whether Redis accepted it."""
    r = _resolve_redis_client(redis_client)
    if r is None:
        return False
    ws_id = ws_instance_id or node_conf.WS_INSTANCE_ID
    value = (
        _encode_agent_loc(ws_instance_id=ws_id, session_id=session_id)
        if session_id
        else ws_id
    )
    try:
        r.set(agent_loc_key(agent_id), value, ex=node_conf.AGENT_LOC_TTL_SECONDS)
        return True
    except redis.RedisError as exc:
        logger.warning("failed to record Agent route agent_id=%s: %s", agent_id, exc)
        return False


def touch_agent_location(*, agent_id: int) -> None:
    r = get_redis()
    if r is None:
        return
    r.expire(agent_loc_key(agent_id), node_conf.AGENT_LOC_TTL_SECONDS)


def ensure_agent_location_on_heartbeat(
    *, agent_id: int, session_id: str
) -> bool | None:
    """
    Refresh ``agent_loc`` during an open WSS session.

    ``expire`` alone is insufficient when the lease TTL elapsed while the TCP
    session stayed up (for example under ingest back-pressure). Recreate the key
    so unrelated agents are not shown as reconnecting.
    """
    r = get_redis()
    if r is None:
        return None
    key = agent_loc_key(agent_id)
    value = _encode_agent_loc(
        ws_instance_id=node_conf.WS_INSTANCE_ID,
        session_id=session_id,
    )
    # WATCH makes the ownership check and lease renewal one optimistic
    # transaction. A newly connected session changing the route between GET
    # and EXPIRE causes EXEC to abort instead of allowing an old socket to
    # renew the successor's lease.
    for _attempt in range(2):
        try:
            with r.pipeline() as pipe:
                pipe.watch(key)
                raw = pipe.get(key)
                if raw:
                    _ws_instance, current_session = _decode_agent_loc(str(raw))
                    if current_session and current_session != session_id:
                        pipe.reset()
                        return False
                pipe.multi()
                if raw:
                    pipe.expire(key, node_conf.AGENT_LOC_TTL_SECONDS)
                else:
                    pipe.set(key, value, ex=node_conf.AGENT_LOC_TTL_SECONDS)
                pipe.execute()
                return True
        except redis.WatchError:
            continue
        except redis.RedisError as exc:
            logger.warning(
                "failed to refresh Agent route agent_id=%s: %s", agent_id, exc
            )
            return None
    return None


def is_agent_session_current(
    *,
    agent_id: int,
    session_id: str,
    redis_client: redis.Redis | None | _DefaultRedisClient = _DEFAULT_REDIS_CLIENT,
) -> bool | None:
    """Return route ownership, or ``None`` when Redis cannot be checked."""
    session_id = str(session_id or "").strip()
    if not session_id:
        return False
    r = _resolve_redis_client(redis_client)
    if r is None:
        return None
    try:
        raw = r.get(agent_loc_key(agent_id))
    except redis.RedisError:
        return None
    if not raw:
        return False
    _ws_instance, current_session = _decode_agent_loc(str(raw))
    if current_session is None:
        # Legacy routes stored only the WebSocket instance. They cannot prove
        # ownership, but must not be treated as proof that this session is
        # stale during a rolling upgrade of the control plane.
        return None
    return current_session == session_id


def get_agent_location(*, agent_id: int) -> str | None:
    r = get_redis()
    if r is None:
        return None
    try:
        value = r.get(agent_loc_key(agent_id))
    except redis.RedisError as exc:
        logger.warning("failed to read Agent route agent_id=%s: %s", agent_id, exc)
        return None
    if not value:
        return None
    ws_instance, _session = _decode_agent_loc(str(value))
    return ws_instance


def get_agent_session(*, agent_id: int) -> str | None:
    """Return the authenticated session currently owning an Agent route."""
    r = get_redis()
    if r is None:
        return None
    try:
        value = r.get(agent_loc_key(agent_id))
    except redis.RedisError:
        return None
    if not value:
        return None
    _ws_instance, session = _decode_agent_loc(str(value))
    return session


def clear_agent_location(*, agent_id: int) -> None:
    r = get_redis()
    if r is None:
        return
    r.delete(agent_loc_key(agent_id))


def clear_ws_instance_routes(*, ws_instance_id: str | None = None) -> dict[str, int]:
    """
    Remove stale routing keys owned by one WebSocket process instance.

    Called when a Daphne/WS process starts. If Redis survived a control-plane
    restart, old ``agent_loc`` keys may still point at the same instance id even
    though the Channels groups and TCP sessions are gone.
    """

    ws_id = (ws_instance_id or node_conf.WS_INSTANCE_ID or "").strip()
    if not ws_id:
        return {"agent_locations_deleted": 0, "ws_alive_deleted": 0}
    r = get_redis()
    if r is None:
        return {"agent_locations_deleted": 0, "ws_alive_deleted": 0}

    agent_keys: list[str] = []
    for key in r.scan_iter(match="agent_loc:*", count=500):
        raw = r.get(key)
        if not raw:
            continue
        owner_ws, _session = _decode_agent_loc(str(raw))
        if owner_ws == ws_id:
            agent_keys.append(str(key))

    deleted_agent = 0
    if agent_keys:
        deleted_agent = int(r.delete(*agent_keys) or 0)
    deleted_alive = int(r.delete(ws_alive_key(ws_id)) or 0)
    return {
        "agent_locations_deleted": deleted_agent,
        "ws_alive_deleted": deleted_alive,
    }


def clear_agent_location_if_session(
    *,
    agent_id: int,
    session_id: str,
    redis_client: redis.Redis | None | _DefaultRedisClient = _DEFAULT_REDIS_CLIENT,
) -> bool:
    """
    Remove ``agent_loc`` when it still belongs to ``session_id``.

    Returns True when this session owned the key (or Redis is unavailable).
    """
    r = _resolve_redis_client(redis_client)
    if r is None:
        return True
    key = agent_loc_key(agent_id)
    try:
        result = r.eval(
            """
            local raw = redis.call('get', KEYS[1])
            if not raw then return 1 end
            local decoded, payload = pcall(cjson.decode, raw)
            if decoded and type(payload) == 'table' and payload['session'] then
                if tostring(payload['session']) ~= ARGV[1] then return 0 end
            end
            redis.call('del', KEYS[1])
            return 1
            """,
            1,
            key,
            session_id,
        )
        return bool(result)
    except redis.RedisError as exc:
        logger.warning("failed to clear Agent route agent_id=%s: %s", agent_id, exc)
        return True


def ws_alive_key(ws_instance_id: str) -> str:
    return f"node_alive:{ws_instance_id}"


def ws_recovery_hold_key() -> str:
    return "node_ws_recovery_hold"


def begin_ws_recovery_hold(*, seconds: int | None = None) -> bool:
    r = get_redis()
    if r is None:
        return False
    duration = max(1, int(seconds or node_conf.WS_RECOVERY_HOLD_SECONDS))
    # Store the process instance, rather than a boolean.  Concurrent blue/green
    # replicas may become ready in any order; an earlier replica must not clear
    # a recovery window most recently opened by a later replica.
    r.set(ws_recovery_hold_key(), node_conf.WS_INSTANCE_ID, ex=duration)
    return True


def ws_recovery_hold_active() -> bool:
    r = get_redis()
    if r is None:
        return True
    try:
        return bool(r.exists(ws_recovery_hold_key()))
    except redis.RedisError as exc:
        # Do not dispatch lifecycle commands while the control-plane route
        # state cannot be read reliably.
        logger.warning("failed to read WebSocket recovery hold: %s", exc)
        return True


def clear_ws_recovery_hold() -> bool:
    r = get_redis()
    if r is None:
        return False
    r.eval(
        """
        local value = redis.call('get', KEYS[1])
        if not value then return 1 end
        if value == ARGV[1] or value == '1' then
            redis.call('del', KEYS[1])
        end
        return 1
        """,
        1,
        ws_recovery_hold_key(),
        node_conf.WS_INSTANCE_ID,
    )
    return True


def has_live_ws_instance() -> bool:
    r = get_redis()
    if r is None:
        return False
    return next(r.scan_iter(match="node_alive:*", count=20), None) is not None


def offline_task_finalization_ready() -> bool:
    """Fail closed until a real WS process is alive and its restart hold elapsed."""
    return has_live_ws_instance() and not ws_recovery_hold_active()


def touch_ws_instance_alive(
    *,
    redis_client: redis.Redis | None | _DefaultRedisClient = _DEFAULT_REDIS_CLIENT,
) -> None:
    r = _resolve_redis_client(redis_client)
    if r is None:
        return
    ws_id = node_conf.WS_INSTANCE_ID
    try:
        r.set(ws_alive_key(ws_id), "1", ex=node_conf.WS_INSTANCE_ALIVE_TTL_SECONDS)
    except redis.RedisError as exc:
        logger.warning("failed to renew WebSocket instance lease: %s", exc)


def task_stream_key(task_id: str) -> str:
    return f"task_stream:{task_id}"


def task_stream_waiters_key(task_id: str) -> str:
    return f"task_stream_waiters:{task_id}"


def task_info_key(task_id: str) -> str:
    return f"task_info:{task_id}"


def task_uplink_activity_key(task_id: str) -> str:
    """Return the short-lived marker key for queued Agent task uplink."""
    return f"task_uplink_activity:{task_id}"


def periodic_lease_key(name: str) -> str:
    """Return the Redis key used to coalesce one periodic task family."""
    return f"periodic_lease:{name}"


def lifecycle_advance_event_key(*, node_id: int) -> str:
    """Return the short-lived coalescing key for one Node lifecycle wake-up."""
    return f"node_lifecycle_event:{int(node_id)}"


def claim_lifecycle_advance_event(
    *,
    node_id: int,
    redis_client: redis.Redis | None | _DefaultRedisClient = _DEFAULT_REDIS_CLIENT,
) -> bool:
    """Claim one lifecycle wake-up for a flapping node.

    Redis also backs the Celery broker, so an unavailable connection fails
    closed instead of creating an enqueue/log storm.  PostgreSQL keeps the
    lifecycle state durable, and the periodic sweep resumes it after Redis
    recovery.  Redis coalesces duplicate callbacks atomically while healthy.
    """
    r = _resolve_redis_client(redis_client)
    if r is None:
        return False
    try:
        return bool(
            r.set(
                lifecycle_advance_event_key(node_id=node_id),
                "1",
                nx=True,
                ex=max(1, int(node_conf.LIFECYCLE_EVENT_COALESCE_SECONDS)),
            )
        )
    except redis.RedisError as exc:
        logger.warning(
            "lifecycle event coalescing unavailable node_id=%s: %s",
            node_id,
            exc,
        )
        return False


@dataclass
class PeriodicLease:
    """Token-owned Redis lease that can be safely renewed by long sweeps."""

    client: redis.Redis | None
    name: str
    key: str
    token: str
    ttl_seconds: int
    acquired: bool = False
    _closed: bool = field(default=False, init=False, repr=False)
    _state_lock: threading.Lock = field(
        default_factory=threading.Lock,
        init=False,
        repr=False,
    )
    _stop_event: threading.Event = field(
        default_factory=threading.Event,
        init=False,
        repr=False,
    )
    _heartbeat_thread: threading.Thread | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __bool__(self) -> bool:
        with self._state_lock:
            return self.acquired and not self._closed

    def refresh(self) -> bool:
        """Extend this lease only while its token still owns the key."""
        with self._state_lock:
            if not self.acquired or self._closed or self.client is None:
                return False
            client = self.client
        try:
            renewed = bool(
                client.eval(
                    """
                    if redis.call('get', KEYS[1]) == ARGV[1] then
                        return redis.call('expire', KEYS[1], ARGV[2])
                    end
                    return 0
                    """,
                    1,
                    self.key,
                    self.token,
                    self.ttl_seconds,
                )
            )
        except redis.RedisError as exc:
            logger.warning("periodic lease refresh failed name=%s: %s", self.name, exc)
            renewed = False
        with self._state_lock:
            if self._closed:
                return False
            self.acquired = renewed
            return renewed

    def start_heartbeat(self) -> None:
        """Renew this lease in the background while its protected body runs."""
        if not self:
            return
        interval = max(0.25, self.ttl_seconds / 3)

        def heartbeat() -> None:
            while not self._stop_event.wait(interval):
                if not self.refresh():
                    return

        self._heartbeat_thread = threading.Thread(
            target=heartbeat,
            name=f"periodic-lease-{self.name}",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def release(self) -> None:
        """Release this lease without deleting a successor's token."""
        with self._state_lock:
            self._closed = True
            client = self.client
        self._stop_event.set()
        heartbeat_thread = self._heartbeat_thread
        if heartbeat_thread is not None:
            heartbeat_thread.join(timeout=1)
        if client is None:
            return
        try:
            client.eval(
                """
                if redis.call('get', KEYS[1]) == ARGV[1] then
                    return redis.call('del', KEYS[1])
                end
                return 0
                """,
                1,
                self.key,
                self.token,
            )
        except redis.RedisError as exc:
            logger.warning("periodic lease release failed name=%s: %s", self.name, exc)
        finally:
            with self._state_lock:
                self.acquired = False


@contextmanager
def periodic_lease(*, name: str, ttl_seconds: int) -> Iterator[PeriodicLease]:
    """Acquire a crash-safe periodic-task lease, failing closed without Redis."""
    r = get_redis()
    lease = PeriodicLease(
        client=r,
        name=name,
        key=periodic_lease_key(name),
        token=uuid.uuid4().hex,
        ttl_seconds=max(1, int(ttl_seconds)),
    )
    if r is None:
        yield lease
        return

    try:
        lease.acquired = bool(
            r.set(
                lease.key,
                lease.token,
                nx=True,
                ex=lease.ttl_seconds,
            )
        )
    except redis.RedisError as exc:
        logger.warning("periodic lease unavailable name=%s: %s", name, exc)
        yield lease
        return

    try:
        lease.start_heartbeat()
        yield lease
    finally:
        lease.release()


def enqueue_uplink_with_activity(
    client: redis.Redis,
    *,
    stream_name: str,
    fields: dict[str, str],
    task_id: str,
    message_type: str,
    marker_token: str,
) -> None:
    """Atomically enqueue task uplink and publish its projection marker."""
    pipeline = client.pipeline(transaction=True)
    pipeline.xadd(stream_name, fields)
    stage_task_uplink_activity(
        pipeline,
        task_id=task_id,
        message_type=message_type,
        marker_token=marker_token,
    )
    pipeline.execute()


def stage_task_uplink_activity(
    pipeline,
    *,
    task_id: str,
    message_type: str,
    marker_token: str,
) -> None:
    """Stage a task marker in an existing Redis transaction pipeline."""
    payload, ttl_seconds = task_uplink_activity_record(
        message_type=message_type,
        marker_token=marker_token,
    )
    pipeline.set(
        task_uplink_activity_key(task_id),
        payload,
        ex=ttl_seconds,
    )


def task_uplink_activity_record(
    *,
    message_type: str,
    marker_token: str,
) -> tuple[str, int]:
    """Return the serialized marker and TTL shared by enqueue and replay."""
    payload = {
        "marker_token": str(marker_token),
        "message_type": str(message_type),
        "received_at": time.time(),
    }
    return (
        json.dumps(payload, ensure_ascii=False),
        max(60, node_conf.TASK_RESULT_UPLINK_PROJECTION_GRACE_SECONDS * 2),
    )


def clear_task_uplink_activity(*, task_id: str, marker_token: str) -> None:
    """Clear a task marker only when it still belongs to this stream entry."""
    r = get_redis()
    if r is None:
        return
    try:
        r.eval(
            """
            local raw = redis.call('get', KEYS[1])
            if not raw then return 0 end
            local decoded, payload = pcall(cjson.decode, raw)
            if decoded and type(payload) == 'table'
                and tostring(payload['marker_token'] or '') == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """,
            1,
            task_uplink_activity_key(task_id),
            str(marker_token),
        )
    except redis.RedisError as exc:
        logger.warning("task uplink marker cleanup failed task=%s: %s", task_id, exc)


def get_task_uplink_activity(*, task_id: str) -> dict[str, Any] | None:
    """Return queued Agent task activity, if its short-lived marker exists."""
    return get_task_uplink_activities(task_ids=[task_id]).get(str(task_id))


def get_task_uplink_activities(*, task_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Batch-read queued Agent task activity without holding database locks."""
    normalized_ids = [str(task_id) for task_id in task_ids if task_id]
    if not normalized_ids:
        return {}
    r = get_redis()
    if r is None:
        return {}
    try:
        pipeline = r.pipeline(transaction=False)
        for task_id in normalized_ids:
            pipeline.get(task_uplink_activity_key(task_id))
        values = pipeline.execute()
    except redis.RedisError as exc:
        logger.warning("task uplink marker batch read failed: %s", exc)
        return {}
    activities: dict[str, dict[str, Any]] = {}
    for task_id, raw in zip(normalized_ids, values, strict=True):
        if not raw:
            continue
        try:
            payload = json.loads(str(raw))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            activities[task_id] = payload
    return activities


def register_task_stream_waiter(*, task_id: str, ttl_seconds: int) -> str | None:
    """Register one token-owned waiter without shortening a peer lease."""
    r = get_redis()
    if r is None:
        return None
    ttl = max(1, int(ttl_seconds))
    waiter_token = uuid.uuid4().hex
    try:
        r.eval(
            """
            redis.call('hset', KEYS[1], ARGV[1], '1')
            local current_ttl = redis.call('ttl', KEYS[1])
            if current_ttl < tonumber(ARGV[2]) then
                redis.call('expire', KEYS[1], ARGV[2])
            end
            return redis.call('hlen', KEYS[1])
            """,
            1,
            task_stream_waiters_key(task_id),
            waiter_token,
            ttl,
        )
    except redis.RedisError as exc:
        logger.warning(
            "task stream waiter registration failed task=%s: %s", task_id, exc
        )
        return None
    return waiter_token


def unregister_task_stream_waiter(*, task_id: str, waiter_token: str) -> None:
    """Release only this token and remove notifications after the last waiter exits."""
    r = get_redis()
    if r is None:
        return
    try:
        r.eval(
            """
            redis.call('hdel', KEYS[1], ARGV[1])
            local count = redis.call('hlen', KEYS[1])
            if count == 0 then
                redis.call('del', KEYS[1])
                redis.call('del', KEYS[2])
                return 0
            end
            return count
            """,
            2,
            task_stream_waiters_key(task_id),
            task_stream_key(task_id),
            str(waiter_token),
        )
    except redis.RedisError as exc:
        logger.warning("task stream waiter release failed task=%s: %s", task_id, exc)


@contextmanager
def task_stream_waiter(*, task_id: str, ttl_seconds: int) -> Iterator[bool]:
    """Keep a bounded notification channel alive for one synchronous waiter."""
    waiter_token = register_task_stream_waiter(
        task_id=task_id,
        ttl_seconds=ttl_seconds,
    )
    try:
        yield waiter_token is not None
    finally:
        if waiter_token is not None:
            unregister_task_stream_waiter(
                task_id=task_id,
                waiter_token=waiter_token,
            )


def push_task_stream(*, task_id: str, message: dict[str, Any]) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        r.eval(
            """
            if redis.call('exists', KEYS[1]) == 0 then return 0 end
            redis.call('lpush', KEYS[2], ARGV[1])
            redis.call('ltrim', KEYS[2], 0, tonumber(ARGV[2]) - 1)
            local waiter_ttl = redis.call('ttl', KEYS[1])
            local notification_ttl = math.max(waiter_ttl, tonumber(ARGV[3]))
            redis.call('expire', KEYS[2], notification_ttl)
            return 1
            """,
            2,
            task_stream_waiters_key(task_id),
            task_stream_key(task_id),
            json.dumps(message, ensure_ascii=False),
            node_conf.TASK_STREAM_MAX_MESSAGES,
            node_conf.TASK_STREAM_TTL_GRACE_SECONDS,
        )
    except redis.RedisError as exc:
        # PostgreSQL remains authoritative; losing a wake-up only adds one poll interval.
        logger.warning("task stream notification failed task=%s: %s", task_id, exc)


def bpop_task_stream(
    *, task_id: str, timeout_seconds: int = 15
) -> dict[str, Any] | None:
    r = get_redis()
    if r is None:
        return None
    try:
        item = r.blpop(task_stream_key(task_id), timeout=max(1, int(timeout_seconds)))
    except RedisTimeoutError as exc:
        logger.warning(
            "node redis task stream wait timed out for task %s: %s", task_id, exc
        )
        return None
    except redis.RedisError as exc:
        logger.warning(
            "node redis task stream unavailable for task %s: %s", task_id, exc
        )
        return None
    if not item:
        return None
    _, raw = item
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def set_task_info(
    *, task_id: str, data: dict[str, Any], ttl_seconds: int = 3600
) -> None:
    r = get_redis()
    if r is None:
        return
    try:
        r.set(
            task_info_key(task_id),
            json.dumps(data, ensure_ascii=False),
            ex=max(60, int(ttl_seconds)),
        )
    except redis.RedisError as exc:
        # PostgreSQL is authoritative. A missing hot projection only adds a
        # polling interval and must not roll back durable task state.
        logger.warning("task info projection failed task=%s: %s", task_id, exc)


def get_task_info(*, task_id: str) -> dict[str, Any] | None:
    r = get_redis()
    if r is None:
        return None
    try:
        raw = r.get(task_info_key(task_id))
    except redis.RedisError as exc:
        logger.warning("task info projection unavailable task=%s: %s", task_id, exc)
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None
