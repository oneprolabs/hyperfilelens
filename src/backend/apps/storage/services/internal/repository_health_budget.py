"""Health-only deadlines; ordinary storage operations retain their budgets."""

import time
from contextlib import contextmanager
from contextvars import ContextVar

_deadline = ContextVar("repository_health_deadline", default=None)


def remaining_health_seconds(default: float) -> float:
    deadline = _deadline.get()
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Repository health check execution budget exhausted.")
    return min(default, remaining)


def health_budget_active() -> bool:
    return _deadline.get() is not None


@contextmanager
def repository_health_budget(seconds: int):
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)
