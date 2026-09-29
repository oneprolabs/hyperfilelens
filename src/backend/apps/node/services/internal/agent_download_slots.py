"""Bound Agent release download authorizations by organization.

Keep the existing Redis Set/key so blue-green API versions share one quota.
The key's TTL is a fallback for interrupted installations, not the normal
release mechanism for completed installation sessions.
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
# Match the installation session idle lease. A shorter Set TTL could erase a
# still-valid authorization before its normal Session lifecycle completes.
DEFAULT_SLOT_TTL_SECONDS = 6 * 60 * 60

_ACQUIRE_LUA = """
local key = KEYS[1]
local slot = ARGV[1]
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

if redis.call('SISMEMBER', key, slot) == 1 then
  return {1, redis.call('SCARD', key)}
end

local count = redis.call('SCARD', key)
if count >= limit then
  return {0, count}
end

redis.call('SADD', key, slot)
-- An existing key's expiration is never extended by another installation.
-- Also recover a pre-existing key that was left without an expiration.
if redis.call('TTL', key) == -1 then
  redis.call('EXPIRE', key, ttl)
end
return {1, count + 1}
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
        return True, 0  # Retain the existing fail-open behavior.
    try:
        allowed, count = client.eval(
            _ACQUIRE_LUA, 1, _key(organization_key), slot_id, str(limit), str(ttl)
        )
        return bool(int(allowed)), int(count)
    except _REDIS_ERRORS:
        logger.warning("Agent download slot acquisition unavailable", exc_info=True)
        return True, 0


def release_session_slot(organization_key: str, session_id: int) -> None:
    """Best-effort, idempotent cleanup after a committed session transition."""
    client = _redis_client()
    if client is None:
        return
    try:
        client.srem(_key(organization_key), f"session:{session_id}")
    except _REDIS_ERRORS:
        logger.warning("Agent download session slot cleanup unavailable", exc_info=True)
