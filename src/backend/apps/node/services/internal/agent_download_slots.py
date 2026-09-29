"""Bound Agent download authorizations with independently expiring slots.

The legacy Redis Set remains the quota authority across a rolling API upgrade.
A companion sorted set tracks each slot's deadline so a newer installation
does not extend an abandoned slot or erase a still-recent authorization.
"""

from __future__ import annotations

import logging
import os
from typing import Any

try:
    import redis
except ImportError:  # pragma: no cover
    redis = None

logger = logging.getLogger(__name__)

_REDIS_ERRORS: tuple[type[BaseException], ...] = (OSError, TypeError, ValueError)
if redis is not None:
    _REDIS_ERRORS += (redis.exceptions.RedisError,)

DEFAULT_LIMIT = 20
RETRY_AFTER_SECONDS = 30
# A successful authenticated download renews its own slot, matching the
# installation session's idle lease. Other slots never inherit that renewal.
DEFAULT_SLOT_TTL_SECONDS = 6 * 60 * 60


class DownloadSlotUnavailable(Exception):
    """New download authorizations cannot be counted safely without Redis."""


_ACQUIRE_LUA = """
local key = KEYS[1]
local deadlines = KEYS[2]
local slot = ARGV[1]
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local now = tonumber(redis.call('TIME')[1])

-- Existing Set members were issued by the old API (or were added while an
-- old and a new API instance were both serving traffic). Give each untracked
-- member only the remaining legacy Set lifetime, not a fresh lease.
local legacy_ttl_ms = redis.call('PTTL', key)
for _, member in ipairs(redis.call('SMEMBERS', key)) do
  if not redis.call('ZSCORE', deadlines, member) then
    local remaining = legacy_ttl_ms >= 0 and math.ceil(legacy_ttl_ms / 1000) or ttl
    redis.call('ZADD', deadlines, now + remaining, member)
  end
end

-- Remove individually expired authorizations before testing capacity.
for _, member in ipairs(redis.call('ZRANGEBYSCORE', deadlines, '-inf', now)) do
  redis.call('SREM', key, member)
  redis.call('ZREM', deadlines, member)
end

-- An older API may have expired the shared Set before the new API's
-- per-slot deadlines. Rebuild the Set from all still-live deadlines before
-- counting, so a mixed-version rollout cannot lose active authorizations.
for _, member in ipairs(redis.call('ZRANGE', deadlines, 0, -1)) do
  redis.call('SADD', key, member)
end

local count = redis.call('SCARD', key)
local allowed = 1
if redis.call('SISMEMBER', key, slot) == 1 then
  -- This slot has just passed application authorization. Renew only itself.
  redis.call('ZADD', deadlines, now + ttl, slot)
elseif count >= limit then
  allowed = 0
else
  redis.call('SADD', key, slot)
  redis.call('ZADD', deadlines, now + ttl, slot)
  count = count + 1
end

-- The Set is never allowed to expire before its latest member. TTLs are
-- storage cleanup only; expired individual members are removed above.
local latest = redis.call('ZREVRANGE', deadlines, 0, 0, 'WITHSCORES')
if latest[2] then
  local remaining = tonumber(latest[2]) - now
  if redis.call('TTL', key) < remaining then
    redis.call('EXPIRE', key, remaining)
  end
  redis.call('EXPIRE', deadlines, remaining)
end
return {allowed, count}
"""

_RELEASE_LUA = """
local removed = redis.call('SREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return removed
"""


def _redis_client() -> Any:
    """Return the optional quota Redis client."""
    if redis is None:
        return None
    url = (
        os.getenv("AGENT_RELEASES_REDIS_URL")
        or os.getenv("CACHE_REDIS_URL")
        or os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
    )
    try:
        return redis.Redis.from_url(url, decode_responses=True)
    except _REDIS_ERRORS:
        return None


def _key(organization_key: str) -> str:
    """Keep the legacy key so mixed-version API pools share one quota."""
    return f"hfl:agent-releases:slots:{organization_key}"


def _deadlines_key(organization_key: str) -> str:
    """Track deadlines beside the existing shared quota key."""
    return f"{_key(organization_key)}:deadlines"


def slot_limit() -> int:
    """Return the configured organization-level authorization limit."""
    return int(
        os.getenv("AGENT_RELEASES_TENANT_MAX_CONCURRENT_DOWNLOADS", str(DEFAULT_LIMIT))
    )


def try_acquire_slot(organization_key: str, slot_id: str) -> tuple[bool, int]:
    """Atomically accept at most the configured number of distinct slot IDs."""
    limit = slot_limit()
    ttl = int(
        os.getenv("AGENT_RELEASES_SLOT_TTL_SECONDS", str(DEFAULT_SLOT_TTL_SECONDS))
    )
    client = _redis_client()
    if client is None:
        logger.warning("Agent download slot acquisition unavailable: Redis client missing")
        raise DownloadSlotUnavailable("Agent download authorization is unavailable")
    try:
        allowed, count = client.eval(
            _ACQUIRE_LUA,
            2,
            _key(organization_key),
            _deadlines_key(organization_key),
            slot_id,
            str(limit),
            str(ttl),
        )
        return bool(int(allowed)), int(count)
    except _REDIS_ERRORS as exc:
        logger.warning("Agent download slot acquisition unavailable", exc_info=True)
        raise DownloadSlotUnavailable("Agent download authorization is unavailable") from exc


def release_session_slot(organization_key: str, session_id: int) -> None:
    """Best-effort, idempotent cleanup after a committed session transition."""
    client = _redis_client()
    if client is None:
        return
    try:
        client.eval(
            _RELEASE_LUA,
            2,
            _key(organization_key),
            _deadlines_key(organization_key),
            f"session:{session_id}",
        )
    except _REDIS_ERRORS:
        logger.warning("Agent download session slot cleanup unavailable", exc_info=True)
