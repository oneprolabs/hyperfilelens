"""Small, non-invasive SourceLens probes for the Runtime Environment page.

Log evidence is not a database integrity check or a container-state probe.
Redis access is opt-in and only uses PING and LLEN, never task contents.
"""

from __future__ import annotations

import hashlib
import heapq
import os
import re
import stat
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.core.cache import cache

from apps.lens_bridge import deploy
from apps.lens_bridge.services import sl_client

CACHE_SECONDS = 30
MAX_FILES_PER_DIRECTORY = 4
MAX_DIRECTORY_ENTRIES = 512
MAX_TAIL_BYTES = 256 * 1024
RECENT_ERROR_HOURS = 24
TIMESTAMP = re.compile(
    r"^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
    r"(?:[.,](\d{1,6}))?(?:\s*(UTC|GMT|Z|[+-]\d{2}:?\d{2}))?"
)
INDEX_ERRORS = ("unexpected zero page", "right sibling's left-link doesn't match")


def _notice(code: str, level: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "level": level, "params": params}


def _event_time(line: str) -> datetime | None:
    """Parse PostgreSQL/Celery timestamps; packaged SourceLens logs use UTC."""
    match = TIMESTAMP.match(line)
    if not match:
        return None
    stamp, fraction, zone = match.groups()
    try:
        return datetime.fromisoformat(
            stamp.replace(" ", "T")
            + (f".{fraction}" if fraction else "")
            + (zone if zone and zone not in {"UTC", "GMT", "Z"} else "+00:00")
        )
    except ValueError:
        return None


def _log_files(directory: Path) -> list[Path]:
    """Bound directory enumeration and only inspect regular non-symlink files."""
    candidates: list[tuple[float, Path]] = []
    with os.scandir(directory) as entries:
        for entry in islice(entries, MAX_DIRECTORY_ENTRIES):
            if not entry.name.endswith((".log", ".log.1", ".log.2", ".log.3")):
                continue
            if entry.is_file(follow_symlinks=False):
                candidates.append(
                    (entry.stat(follow_symlinks=False).st_mtime, Path(entry.path))
                )
    return [path for _, path in heapq.nlargest(MAX_FILES_PER_DIRECTORY, candidates)]


def _tail(path: Path) -> str:
    """Read a bounded tail, avoiding symlinks and blocking on special files."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        size = os.fstat(stream.fileno())
        if not stat.S_ISREG(size.st_mode):
            raise OSError("Not a regular log file")
        offset = max(0, size.st_size - MAX_TAIL_BYTES)
        stream.seek(offset)
        data = stream.read(MAX_TAIL_BYTES)
    if offset:
        data = data.partition(b"\n")[2]  # Discard the partial first line.
    return data.decode("utf-8", errors="replace")


def probe_index_errors(root: Path, *, now: datetime) -> dict[str, Any]:
    """Report recent errors separately from historical or undated evidence."""
    latest: datetime | None = None
    undated = False
    readable = False
    incomplete = False
    for name in ("postgresql", "worker"):
        try:
            files = _log_files(root / name)
        except OSError:
            incomplete = True
            continue
        for path in files:
            try:
                text = _tail(path)
            except OSError:
                incomplete = True
                continue
            readable = True
            event_at = None
            for line in text.splitlines():
                stamp = _event_time(line)
                if TIMESTAMP.match(line):
                    event_at = stamp
                if not any(signature in line.lower() for signature in INDEX_ERRORS):
                    continue
                if event_at is None or event_at > now + timedelta(minutes=5):
                    undated = True
                elif latest is None or event_at > latest:
                    latest = event_at

    notices = []
    health = "unknown"  # Absence of log errors never proves database integrity.
    if latest:
        recent = latest >= now - timedelta(hours=RECENT_ERROR_HOURS)
        health = "error" if recent else "unknown"
        notices.append(
            _notice(
                "index_corruption" if recent else "index_corruption_history",
                "error" if recent else "info",
                last_seen_at=latest.isoformat(),
            )
        )
    elif undated:
        notices.append(_notice("index_corruption_undated", "info"))
    if not readable or incomplete:
        notices.append(_notice("index_logs_unavailable", "info"))
    return {
        "health_status": health,
        "availability_status": "unknown",
        "notices": notices,
    }


def _queue_config() -> tuple[list[str], int]:
    raw = os.getenv("HFL_SL_RUNTIME_QUEUES", "lens,sourcelens")
    queues = list(
        dict.fromkeys(item.strip() for item in raw.split(",") if item.strip())
    )
    try:
        threshold = int(os.getenv("HFL_SL_RUNTIME_QUEUE_WARNING", "1000"))
    except ValueError:
        threshold = 0
    if (
        not queues
        or len(queues) > 8
        or any(len(item) > 128 for item in queues)
        or threshold < 1
    ):
        raise ValueError("Invalid queue monitor configuration")
    return queues, threshold


def probe_queue_backlog(url: str) -> dict[str, Any]:
    """Measure configured Celery queues without consuming or enumerating tasks."""
    result: dict[str, Any] = {
        "health_status": "unknown",
        "availability_status": "unknown",
        "notices": [],
    }
    if not url:
        result["notices"] = [_notice("queue_not_configured", "info")]
        return result
    try:
        parsed = urlsplit(url)
        # URL query options can override socket timeouts in redis-py.
        if (
            parsed.scheme not in {"redis", "rediss"}
            or not parsed.hostname
            or parsed.query
        ):
            raise ValueError("Invalid Redis monitor URL")
        queues, threshold = _queue_config()
    except ValueError:
        result["notices"] = [_notice("queue_config_invalid", "warning")]
        return result

    import redis
    from redis.backoff import NoBackoff
    from redis.retry import Retry

    client = None
    try:
        client = redis.from_url(
            url,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=False,
            retry=Retry(NoBackoff(), 0),
        )
        with client.pipeline(transaction=False) as pipeline:
            pipeline.ping()
            for queue in queues:
                pipeline.llen(queue)
            response = pipeline.execute()
        if len(response) != len(queues) + 1 or not response[0]:
            raise ValueError("Invalid queue probe response")
        result["health_status"] = "ok"
        lengths = {}
        for queue, length in zip(queues, response[1:], strict=True):
            if not isinstance(length, int) or length < 0:
                raise ValueError("Invalid queue length")
            lengths[queue] = length
            if length >= threshold:
                result["notices"].append(
                    _notice(
                        "queue_backlog",
                        "warning",
                        queue=queue,
                        count=length,
                        threshold=threshold,
                    )
                )
        result["queue_lengths"] = lengths
        if result["notices"]:
            result["availability_status"] = "degraded"
    except (redis.RedisError, OSError, ValueError):
        # Never return credentials, endpoints or Redis exception messages.
        result["health_status"] = "error"
        result["notices"] = [_notice("queue_probe_failed", "warning")]
    finally:
        if client is not None:
            client.close()
    return result


def _collect_health(*, timeout: int) -> dict[str, Any]:
    health = dict(sl_client.ping(timeout=timeout))
    now = datetime.now(timezone.utc)
    root = os.getenv("HFL_SL_RUNTIME_LOG_DIR", "").strip()
    components = {}
    if deploy.sourcelens_mode() == "bundled" or root:
        components["postgres"] = probe_index_errors(
            Path(root or "/var/log/sourcelens"),
            now=now,
        )
    url = os.getenv("HFL_SL_RUNTIME_REDIS_URL", "").strip()
    if deploy.sourcelens_mode() == "bundled" or url:
        components["redis"] = probe_queue_backlog(url)
    notices = [notice for probe in components.values() for notice in probe["notices"]]
    api_status = (
        "unknown"
        if not health.get("configured")
        else "error"
        if not health.get("reachable")
        else "ok"
        if health.get("business_ready")
        else "degraded"
    )
    has_alert = any(notice["level"] in {"warning", "error"} for notice in notices)
    status = "degraded" if api_status == "ok" and has_alert else api_status
    health_status = (
        "error"
        if api_status == "error"
        or any(probe["health_status"] == "error" for probe in components.values())
        else status
    )
    health["runtime_monitor"] = {
        "status": status,
        "health_status": health_status,
        "components": components,
        "checked_at": now.isoformat(),
    }
    return health


def sourcelens_health_payload(*, timeout: int = 3) -> dict[str, Any]:
    """Share one short-lived snapshot across Environment and Integrations."""
    config = "\0".join(
        [
            deploy.sourcelens_mode(),
            deploy.lens_base_url(),
            deploy.lens_bridge_email(),
            deploy.lens_bridge_password(),
            os.getenv("HFL_SL_RUNTIME_LOG_DIR", ""),
            os.getenv("HFL_SL_RUNTIME_REDIS_URL", ""),
            os.getenv("HFL_SL_RUNTIME_QUEUES", ""),
            os.getenv("HFL_SL_RUNTIME_QUEUE_WARNING", ""),
        ]
    )
    key = "runtime:sl:v1:" + hashlib.sha256(config.encode()).hexdigest()
    try:
        cached = cache.get(key)
    except Exception:  # Cache failure must not prevent runtime diagnostics.
        cached = None
    if cached is not None:
        return cached
    payload = _collect_health(timeout=timeout)
    try:
        cache.set(key, payload, CACHE_SECONDS)
    except Exception:
        pass
    return payload
