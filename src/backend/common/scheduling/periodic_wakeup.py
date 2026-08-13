"""Broker-side single-flight guard for disposable Celery Beat wake-ups."""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from functools import lru_cache
from typing import Any

from celery import signals, states
from django.conf import settings
from redis import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

PERIODIC_WAKEUP_TOKEN_HEADER = "hfl_periodic_wakeup_token"
PERIODIC_WAKEUP_NAME_HEADER = "hfl_periodic_wakeup_name"
PERIODIC_WAKEUP_COALESCE_HEADER = "hfl_periodic_wakeup_coalesce"
_KEY_PREFIX = "hfl:periodic-wakeup:"
_CLAIMING_PREFIX = "claiming:"
_QUEUED_PREFIX = "queued:"
_RUNNING_PREFIX = "running:"
_TOKEN_PATTERN = re.compile(r"[0-9]+:[0-9a-f]{32}")


def _lease_key(name: str) -> str:
    digest = hashlib.sha256(str(name).encode("utf-8")).hexdigest()
    return f"{_KEY_PREFIX}{digest}"


def _normalize_token(token: str) -> str:
    """Return a valid wake-up ownership token or an empty string."""
    value = str(token or "").strip()
    return value if _TOKEN_PATTERN.fullmatch(value) else ""


@lru_cache(maxsize=1)
def _redis_client() -> Redis:
    return Redis.from_url(
        settings.CELERY_BROKER_URL,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


def claim_periodic_wakeup(name: str) -> str | bool | None:
    """Take a short publish claim; it is promoted only after broker acceptance."""
    queue_deadline = int(time.time()) + max(
        60,
        int(settings.CELERY_PERIODIC_WAKEUP_MAX_QUEUE_SECONDS),
    )
    token = f"{queue_deadline}:{uuid.uuid4().hex}"
    key = _lease_key(name)
    try:
        client = _redis_client()
        acquired = client.set(
            key,
            f"{_CLAIMING_PREFIX}{token}",
            nx=True,
            ex=max(5, int(settings.CELERY_PERIODIC_WAKEUP_CLAIM_SECONDS)),
        )
        if not acquired:
            # Renew only broker-accepted queued work. A publisher that dies
            # before broker acceptance leaves a short claiming lease that must
            # expire naturally. Running work also has an absolute lease so a
            # lost worker or failed post-run release self-heals.
            client.eval(
                """
local current = redis.call('get', KEYS[1])
local deadline = current and string.match(current, '^queued:(%d+):')
if deadline then
  local now = tonumber(redis.call('TIME')[1])
  local remaining = tonumber(deadline) - now
  if remaining <= 0 then
    return 0
  end
  local ttl = math.min(tonumber(ARGV[1]), remaining)
  return redis.call('expire', KEYS[1], ttl)
end
return 0
""",
                1,
                key,
                max(60, int(settings.CELERY_PERIODIC_WAKEUP_LEASE_SECONDS)),
            )
    except (RedisError, TypeError, ValueError):
        # Publishing through the same broker will report the authoritative
        # transport error. Do not suppress a due task merely because the guard
        # could not be checked.
        logger.warning("periodic wake-up guard unavailable name=%s", name)
        return None
    return token if acquired else False


def promote_periodic_wakeup(name: str, token: str) -> bool:
    """Promote a publish claim into a renewable lease after broker acceptance."""
    value = _normalize_token(token)
    if not value:
        return False
    script = """
local key = KEYS[1]
local claiming = ARGV[1]
local queued = ARGV[2]
local ttl = ARGV[3]
local current = redis.call('get', key)
if current == claiming then
  local deadline = string.match(queued, '^queued:(%d+):')
  local now = tonumber(redis.call('TIME')[1])
  local remaining = tonumber(deadline) - now
  if remaining <= 0 then
    return 0
  end
  redis.call('set', key, queued, 'EX', math.min(tonumber(ttl), remaining))
  return 1
end
return 0
"""
    try:
        client = _redis_client()
        return (
            int(
                client.eval(
                    script,
                    1,
                    _lease_key(name),
                    f"{_CLAIMING_PREFIX}{value}",
                    f"{_QUEUED_PREFIX}{value}",
                    max(60, int(settings.CELERY_PERIODIC_WAKEUP_LEASE_SECONDS)),
                )
                or 0
            )
            == 1
        )
    except (RedisError, TypeError, ValueError):
        # The message is already durable in the broker. A failed promotion can
        # produce at most one later duplicate after the short claim expires;
        # database idempotency remains authoritative.
        logger.warning("periodic wake-up guard promotion failed name=%s", name)
    return False


def mark_periodic_wakeup_running(name: str, token: str) -> bool:
    """Move one consumed wake-up to a bounded execution lease."""
    value = _normalize_token(token)
    if not value:
        return False
    script = """
local key = KEYS[1]
local claiming = ARGV[1]
local queued = ARGV[2]
local running = ARGV[3]
local ttl = ARGV[4]
local current = redis.call('get', key)
if current == claiming or current == queued then
  redis.call('set', key, running, 'EX', ttl)
  return 1
end
return 0
"""
    try:
        return (
            int(
                _redis_client().eval(
                    script,
                    1,
                    _lease_key(name),
                    f"{_CLAIMING_PREFIX}{value}",
                    f"{_QUEUED_PREFIX}{value}",
                    f"{_RUNNING_PREFIX}{value}",
                    max(
                        60,
                        int(settings.CELERY_TASK_TIME_LIMIT) + 300,
                    ),
                )
                or 0
            )
            == 1
        )
    except (RedisError, TypeError, ValueError):
        logger.warning(
            "periodic wake-up execution lease transition failed name=%s", name
        )
    return False


def requeue_periodic_wakeup(name: str, token: str) -> bool:
    """Move a retrying wake-up back to its bounded queue lease."""
    value = _normalize_token(token)
    if not value:
        return False
    script = """
local key = KEYS[1]
local queued = ARGV[1]
local running = ARGV[2]
local ttl = ARGV[3]
local current = redis.call('get', key)
if current ~= running and current ~= queued then
  return 0
end
local deadline = string.match(queued, '^queued:(%d+):')
local now = tonumber(redis.call('TIME')[1])
local remaining = tonumber(deadline) - now
if remaining <= 0 then
  redis.call('del', key)
  return 0
end
redis.call('set', key, queued, 'EX', math.min(tonumber(ttl), remaining))
return 1
"""
    try:
        return (
            int(
                _redis_client().eval(
                    script,
                    1,
                    _lease_key(name),
                    f"{_QUEUED_PREFIX}{value}",
                    f"{_RUNNING_PREFIX}{value}",
                    max(60, int(settings.CELERY_PERIODIC_WAKEUP_LEASE_SECONDS)),
                )
                or 0
            )
            == 1
        )
    except (RedisError, TypeError, ValueError):
        logger.warning("periodic wake-up retry lease transition failed name=%s", name)
    return False


def release_periodic_wakeup(name: str, token: str) -> bool:
    """Release only the lease owned by ``token``; stale deliveries are fenced."""
    value = _normalize_token(token)
    if not value:
        return False
    script = """
local key = KEYS[1]
local claiming = ARGV[1]
local queued = ARGV[2]
local running = ARGV[3]
local current = redis.call('get', key)
if current == claiming or current == queued or current == running then
  return redis.call('del', key)
end
return 0
"""
    try:
        client = _redis_client()
        return (
            int(
                client.eval(
                    script,
                    1,
                    _lease_key(name),
                    f"{_CLAIMING_PREFIX}{value}",
                    f"{_QUEUED_PREFIX}{value}",
                    f"{_RUNNING_PREFIX}{value}",
                )
                or 0
            )
            == 1
        )
    except (RedisError, TypeError, ValueError):
        logger.warning("periodic wake-up guard release failed")
    return False


def _request_wakeup(request: Any) -> tuple[str, str]:
    stamps = getattr(request, "stamps", None)
    if isinstance(stamps, dict):
        name = str(stamps.get(PERIODIC_WAKEUP_NAME_HEADER) or "").strip()
        token = str(stamps.get(PERIODIC_WAKEUP_TOKEN_HEADER) or "").strip()
        return name, token
    return "", ""


@signals.task_prerun.connect(dispatch_uid="hfl-start-periodic-wakeup-prerun")
def _mark_periodic_wakeup_running(*, task=None, **_kwargs) -> None:
    """Stop queue renewals once a worker starts executing the wake-up."""
    name, token = _request_wakeup(getattr(task, "request", None))
    if name and token:
        mark_periodic_wakeup_running(name, token)


@signals.task_postrun.connect(dispatch_uid="hfl-release-periodic-wakeup-postrun")
def _release_periodic_wakeup_after_run(*, task=None, state=None, **_kwargs) -> None:
    """Release the wake-up lease after execution reaches a terminal result."""
    name, token = _request_wakeup(getattr(task, "request", None))
    if state == states.RETRY:
        # Celery copies stamped headers to the retry message. Return its lease
        # to queued so Beat renews it while that retry waits for a worker.
        if name and token:
            requeue_periodic_wakeup(name, token)
        return
    if name and token:
        release_periodic_wakeup(name, token)


@signals.task_revoked.connect(dispatch_uid="hfl-release-periodic-wakeup-revoked")
def _release_periodic_wakeup_after_revoke(*, request=None, **_kwargs) -> None:
    """Release expired/revoked messages that never reached task execution."""
    name, token = _request_wakeup(request)
    if name and token:
        release_periodic_wakeup(name, token)
