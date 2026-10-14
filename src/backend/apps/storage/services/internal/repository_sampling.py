"""Fixed-cycle staggering and bounded cache fences; no new workflow schema."""

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from django.core.cache import cache


def sampling_slot(repository_id: int, interval: int, *, now=None) -> tuple[int, int]:
    current = int((now or datetime.now(UTC)).timestamp())
    # Leave three minutes for admission/collection before the half-hour closes.
    spread = max(1, interval - min(180, interval // 2))
    offset = int(hashlib.sha256(str(repository_id).encode()).hexdigest()[:8], 16) % spread
    slot = current // interval * interval
    return slot, slot + offset


def reserve_sample(
    repository, kind: str, interval: int, *, now=None, stagger: bool = True,
) -> tuple[str, int] | None:
    current = now or datetime.now(UTC)
    slot, due = sampling_slot(repository.id, interval, now=current)
    if stagger and current.timestamp() < due:
        return None
    success = repository.usage_last_success_at if kind == "usage" else None
    if success and success.timestamp() >= slot:
        return None
    key = f"storage:sampling:{kind}:{repository.organization_id}:{repository.id}:{slot}"
    # One counter survives short deferral cooldowns for this fixed cycle.
    attempts_key = f"{key}:attempts"
    token = uuid4().hex
    if not cache.add(key, token, timeout=interval):
        return None
    if cache.add(attempts_key, 1, timeout=interval * 2):
        attempts = 1
    else:
        try:
            attempts = cache.incr(attempts_key)
        except ValueError:
            # Expiry/cache loss cannot justify proceeding without a fence.
            return None
    if attempts > 3:
        return None
    return key, slot


def retry_delay(attempt: int) -> int:
    """Bounded exponential delay with jitter, independent of repository health."""
    import random

    return min(120, 15 * 2 ** max(0, attempt)) + random.randint(0, 15)


def finish_sample(
    key: str | None, *, successful: bool, deferred: bool = False,
) -> None:
    if key and not successful and deferred:
        # Only admission/publication deferrals reopen the root request. Actual
        # collection failures keep the cycle fence; bounded command retries
        # must not be reset by the minute-level dispatcher.
        cache.touch(key, timeout=120)
