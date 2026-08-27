"""Shared admission control for Controller-local background storage work."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from functools import lru_cache

from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError

from apps.storage.conf import background_storage_concurrency

logger = logging.getLogger(__name__)

_CAPACITY_KEY = "hfl:storage:background-capacity"
_LEASE_SECONDS = 120
_REFRESH_SECONDS = 30
_REFRESH_RETRY_SECONDS = 5
_LOCAL_EXPIRY_SAFETY_SECONDS = 2

_ACQUIRE_SCRIPT = """
local key = KEYS[1]
local token = ARGV[1]
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local now = tonumber(redis.call('TIME')[1])
redis.call('zremrangebyscore', key, '-inf', now)
if redis.call('zcard', key) >= limit then
  return 0
end
redis.call('zadd', key, now + ttl, token)
redis.call('expire', key, ttl * 2)
return 1
"""

_REFRESH_SCRIPT = """
local key = KEYS[1]
local token = ARGV[1]
local ttl = tonumber(ARGV[2])
if not redis.call('zscore', key, token) then
  return 0
end
local now = tonumber(redis.call('TIME')[1])
redis.call('zadd', key, now + ttl, token)
redis.call('expire', key, ttl * 2)
return 1
"""


@lru_cache(maxsize=1)
def _redis_client() -> Redis:
    return Redis.from_url(
        settings.CELERY_BROKER_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


class BackgroundStorageLease:
    """One renewable, fenced slot in the shared background storage budget."""

    def __init__(
        self,
        *,
        token: str,
        operation: str,
        confirmed_at: float | None = None,
    ) -> None:
        self.token = token
        self.operation = operation
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None
        self._confirmed_until = (
            confirmed_at if confirmed_at is not None else time.monotonic()
        ) + _LEASE_SECONDS - _LOCAL_EXPIRY_SAFETY_SECONDS

    @property
    def valid(self) -> bool:
        return not self._lost.is_set()

    def __enter__(self) -> BackgroundStorageLease:
        self._thread = threading.Thread(
            target=self._refresh_loop,
            name=f"storage-background-lease-{self.token[-8:]}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        self.release()

    def _refresh_loop(self) -> None:
        delay = _REFRESH_SECONDS
        while not self._stop.wait(delay):
            outcome = self._refresh_once()
            if outcome is True:
                delay = _REFRESH_SECONDS
                continue
            if outcome is False or time.monotonic() >= self._confirmed_until:
                self._mark_lost()
                return
            delay = min(
                _REFRESH_RETRY_SECONDS,
                max(0.1, self._confirmed_until - time.monotonic()),
            )

    def refresh(self) -> bool:
        """Renew once, returning false for both loss and temporary unavailability."""

        return self._refresh_once() is True

    def _refresh_once(self) -> bool | None:
        confirmed_at = time.monotonic()
        try:
            refreshed = bool(
                int(
                    _redis_client().eval(
                        _REFRESH_SCRIPT,
                        1,
                        _CAPACITY_KEY,
                        self.token,
                        _LEASE_SECONDS,
                    )
                    or 0
                )
            )
        except (RedisError, TypeError, ValueError):
            logger.warning(
                "background storage capacity lease refresh failed operation=%s",
                self.operation,
            )
            return None
        if refreshed:
            self._confirmed_until = (
                confirmed_at + _LEASE_SECONDS - _LOCAL_EXPIRY_SAFETY_SECONDS
            )
        return refreshed

    def _mark_lost(self) -> None:
        self._lost.set()
        logger.error(
            "background storage capacity lease lost operation=%s token=%s",
            self.operation,
            self.token,
        )

    def release(self) -> bool:
        try:
            return bool(_redis_client().zrem(_CAPACITY_KEY, self.token))
        except RedisError:
            logger.warning(
                "background storage capacity lease release failed operation=%s",
                self.operation,
            )
            return False


def try_acquire_background_storage_capacity(
    *, operation: str, identity: str
) -> BackgroundStorageLease | None:
    """Acquire without blocking; unavailable coordination fails closed."""

    token = f"{operation}:{identity}:{uuid.uuid4().hex}"
    acquire_started_at = time.monotonic()
    try:
        acquired = int(
            _redis_client().eval(
                _ACQUIRE_SCRIPT,
                1,
                _CAPACITY_KEY,
                token,
                background_storage_concurrency(),
                _LEASE_SECONDS,
            )
            or 0
        )
    except (RedisError, TypeError, ValueError):
        logger.warning(
            "background storage capacity unavailable operation=%s identity=%s",
            operation,
            identity,
        )
        return None
    if acquired != 1:
        logger.info(
            "background storage capacity full operation=%s identity=%s",
            operation,
            identity,
        )
        return None
    return BackgroundStorageLease(
        token=token,
        operation=operation,
        confirmed_at=acquire_started_at,
    )
