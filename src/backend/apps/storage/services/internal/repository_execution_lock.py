"""Crash-safe, process-wide fencing for long repository operations.

The short Redis advance lock prevents duplicate queue delivery, but it is not
held for the duration of a long S3 cleanup or repository initialization.  A
PostgreSQL session advisory lock fills that gap: another Controller instance
cannot enter the same physical operation, while a crashed worker releases the
lock automatically when its database connection closes.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from typing import Iterator

from django.db import connection


def _lock_id(operation: str, operation_id: int) -> int:
    digest = hashlib.blake2b(
        f"hyperfilelens:{operation}:{int(operation_id)}".encode("utf-8"),
        digest_size=8,
    ).digest()
    value = int.from_bytes(digest, byteorder="big", signed=False)
    # PostgreSQL accepts a signed BIGINT for pg_try_advisory_lock().
    return value - (1 << 64) if value >= (1 << 63) else value


@contextmanager
def repository_execution_lock(
    *, operation: str, operation_id: int
) -> Iterator[bool]:
    """Try to fence one long-running repository operation.

    Production uses PostgreSQL, where the session lock is released on process
    failure.  SQLite and other test databases do not provide this primitive;
    they retain the existing short queue lock and enter directly.
    """

    if connection.vendor != "postgresql":
        yield True
        return

    lock_id = _lock_id(operation, operation_id)
    acquired = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            acquired = bool(cursor.fetchone()[0])
        if not acquired:
            yield False
            return
        yield True
    finally:
        if acquired:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
            except Exception:
                # A broken connection already releases a PostgreSQL session
                # advisory lock. Never hide the operation's original result.
                pass


__all__ = ["repository_execution_lock"]
