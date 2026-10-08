"""Small, non-invasive SourceLens probes for the Runtime Environment page.

Log evidence is not a database integrity check or a container-state probe.
Redis access is opt-in and only uses PING and LLEN, never task contents.
"""

from __future__ import annotations

import hashlib
import heapq
import os
import pickle
import re
import stat
import time
import uuid
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.core.cache import cache, caches
from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache

from apps.lens_bridge import deploy
from apps.lens_bridge.services import sl_client

CACHE_SECONDS = 30
MAX_FILES_PER_DIRECTORY = 4
MAX_DIRECTORY_ENTRIES = 512
MAX_SCAN_BYTES = 256 * 1024
SCAN_STATE_SECONDS = 7 * 24 * 3600
SCAN_LOCK_SECONDS = 60
RECENT_ERROR_HOURS = 24
TIMESTAMP = re.compile(
    r"^\[?(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})"
    r"(?:[.,](\d{1,6}))?(?:\s*(UTC|GMT|Z|[+-]\d{2}:?\d{2}))?"
)
INDEX_ERRORS = ("unexpected zero page", "right sibling's left-link doesn't match")
ERROR_LEVEL = re.compile(r"^\s*:?\s*(?:\[\d+\]\s*)?(?:ERROR|FATAL|PANIC)\s*[:/]")
DATABASE_EXCEPTION = re.compile(
    r"^(?:[\w]+\.)*(?:DatabaseError|InternalError|OperationalError|"
    r"IndexCorrupted|InternalError_):"
)
SAVE_CHECKPOINT = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
return 1
"""
RELEASE_SCAN = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
return redis.call('DEL', KEYS[1])
"""


def _read_scan_checkpoint(key: str) -> dict[str, Any]:
    """Read Redis checkpoints from the same primary used by atomic writes."""
    backend = caches["default"]
    if isinstance(backend, RedisCache):
        namespaced_key = backend.make_and_validate_key(key)
        factory = backend._cache
        raw = factory.get_client(namespaced_key, write=True).get(namespaced_key)
        return factory._serializer.loads(raw) if raw is not None else {}
    return cache.get(key) or {}


def _atomic_scan_update(
    key: str,
    lock_key: str,
    owner: str,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Conditionally save/release this probe's lease in one backend operation.

    Redis uses the existing HFL cache's serializer and primary connection.
    LocMem uses its own shared mutex, including expiry validation. Other cache
    backends fail closed rather than falling back to check-then-write.
    """
    backend = caches["default"]
    namespaced_lock = backend.make_and_validate_key(lock_key)
    if isinstance(backend, RedisCache):
        factory = backend._cache
        client = factory.get_client(namespaced_lock, write=True)
        serialized_owner = factory._serializer.dumps(owner)
        if payload is None:
            return bool(client.eval(RELEASE_SCAN, 1, namespaced_lock, serialized_owner))
        return bool(
            client.eval(
                SAVE_CHECKPOINT,
                2,
                namespaced_lock,
                backend.make_and_validate_key(key),
                serialized_owner,
                factory._serializer.dumps(payload),
                SCAN_STATE_SECONDS,
            )
        )
    if isinstance(backend, LocMemCache):
        serialized_owner = pickle.dumps(owner, backend.pickle_protocol)
        serialized_payload = (
            pickle.dumps(payload, backend.pickle_protocol)
            if payload is not None
            else None
        )
        namespaced_key = backend.make_and_validate_key(key)
        with backend._lock:
            if (
                backend._has_expired(namespaced_lock)
                or backend._cache.get(namespaced_lock) != serialized_owner
            ):
                return False
            if payload is None:
                return backend._delete(namespaced_lock)
            backend._set(namespaced_key, serialized_payload, SCAN_STATE_SECONDS)
            return True
    return False


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


def _log_files(directory: Path) -> tuple[list[Path], bool]:
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
        truncated = next(entries, None) is not None
    return (
        [path for _, path in heapq.nlargest(MAX_FILES_PER_DIRECTORY, candidates)],
        truncated,
    )


def _read_chunk(
    path: Path, previous: dict[str, Any]
) -> tuple[str, dict[str, Any], bool]:
    """Prioritize recent records, then resume within a fixed budget per file."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        size = os.fstat(stream.fileno())
        if not stat.S_ISREG(size.st_mode):
            raise OSError("Not a regular log file")
        identity = f"{size.st_dev}:{size.st_ino}"
        same_file = previous.get("identity") == identity
        offset = int(previous.get("offset", 0)) if same_file else 0
        if offset > size.st_size:  # Copy-truncate rotation.
            offset = 0
            same_file = False
        jump = size.st_size - offset > MAX_SCAN_BYTES
        if jump:
            # Never make today's faults wait behind an old or fast-growing log.
            offset = size.st_size - MAX_SCAN_BYTES
            same_file = False
            stream.seek(offset - 1)
            starts_record = stream.read(1) == b"\n"
        stream.seek(offset)
        data = stream.read(MAX_SCAN_BYTES)
    if jump and not starts_record:
        # The first record may start before the selected window.
        boundary = data.find(b"\n") + 1
        if not boundary:
            return (
                "",
                {
                    "identity": identity,
                    "offset": offset + len(data),
                    "skip_line": True,
                },
                True,
            )
        offset += boundary
        data = data[boundary:]
    end = data.rfind(b"\n") + 1
    skipped = False
    if end:
        data = data[:end]
    elif len(data) == MAX_SCAN_BYTES:
        # Skip oversized lines without interpreting their fragments as records.
        skipped = True
    else:
        data = b""  # Retry an incomplete last record on the next scan.
    state = dict(previous) if same_file else {}
    if skipped or previous.get("skip_line") and same_file:
        first_end = data.find(b"\n") + 1
        text = data[first_end:] if first_end else b""
        state["skip_line"] = not bool(first_end)
        state.pop("event_at", None)
        state["error_context"] = False
    else:
        text = data
    state.update(identity=identity, offset=offset + len(data))
    return (
        text.decode("utf-8", errors="replace"),
        state,
        (state["offset"] < size.st_size or skipped),
    )


def _scan_errors(
    text: str,
    state: dict[str, Any],
    *,
    now: datetime,
) -> tuple[datetime | None, bool]:
    """Only match error messages or database exceptions, never LOG/SQL text."""
    latest = None
    undated = False
    event_at = (
        datetime.fromisoformat(state["event_at"]) if state.get("event_at") else None
    )
    error_context = bool(state.get("error_context"))
    for line in text.splitlines():
        timestamp = TIMESTAMP.match(line)
        level = ERROR_LEVEL.match(line[timestamp.end() :] if timestamp else line)
        if timestamp:
            event_at = _event_time(line)
            error_context = level is not None
        elif level:
            event_at = None
            error_context = True
        exception = DATABASE_EXCEPTION.match(line.strip())
        if not level and not exception:
            continue
        if exception and timestamp is None and not error_context:
            # A standalone exception is evidence, but has no reliable timestamp.
            event_at = None
        payload = line[timestamp.end() :] if timestamp else line
        if not any(signature in payload.lower() for signature in INDEX_ERRORS):
            continue
        if event_at is None or event_at > now + timedelta(minutes=5):
            undated = True
        elif latest is None or event_at > latest:
            latest = event_at
    state["event_at"] = event_at.isoformat() if event_at else None
    state["error_context"] = error_context
    return latest, undated


def probe_index_errors(root: Path, *, now: datetime) -> dict[str, Any]:
    """Serialize checkpoint updates without waiting for another request."""
    key = (
        "runtime:sl:logs:v2:"
        + hashlib.sha256(str(root.absolute()).encode()).hexdigest()
    )
    lock_key = key + ":scan-lock"
    owner = uuid.uuid4().hex
    started = time.monotonic()
    try:
        acquired = cache.add(lock_key, owner, SCAN_LOCK_SECONDS)
    except Exception:
        acquired = False
    try:
        return _probe_index_errors(
            root,
            key=key,
            now=now,
            lease=(lock_key, owner, started) if acquired else None,
        )
    finally:
        # Expired owners must not remove a subsequent request's lock.
        if acquired:
            try:
                _atomic_scan_update(key, lock_key, owner)
            except Exception:
                pass


def _probe_index_errors(
    root: Path,
    *,
    key: str,
    now: datetime,
    lease: tuple[str, str, float] | None,
) -> dict[str, Any]:
    """Read retained evidence; only the active lease holder scans and writes."""
    state_unavailable = False
    try:
        saved = _read_scan_checkpoint(key)
    except Exception:
        saved = {}
        state_unavailable = True
    latest = datetime.fromisoformat(saved["latest"]) if saved.get("latest") else None
    undated = bool(saved.get("undated"))
    retained_alert = bool(saved.get("retained_alert"))
    previous_files = saved.get("files", {})
    files_state = {}
    readable = False
    incomplete = False
    pending = False
    for name in ("postgresql", "worker") if lease else ():
        try:
            files, truncated = _log_files(root / name)
            pending |= truncated
        except OSError:
            incomplete = True
            continue
        for path in files:
            try:
                text, position, more = _read_chunk(
                    path, previous_files.get(str(path), {})
                )
            except OSError:
                incomplete = True
                continue
            readable = True
            pending |= more
            found, unknown_time = _scan_errors(text, position, now=now)
            files_state[str(path)] = position
            undated |= unknown_time
            if found and (latest is None or found > latest):
                latest = found
    if latest and latest >= now - timedelta(hours=RECENT_ERROR_HOURS):
        retained_alert = True
    if lease:
        lock_key, owner, started = lease
        try:
            if time.monotonic() - started >= SCAN_LOCK_SECONDS - 10:
                state_unavailable = True
            else:
                state_unavailable |= not _atomic_scan_update(
                    key,
                    lock_key,
                    owner,
                    {
                        "files": files_state,
                        "latest": latest.isoformat() if latest else None,
                        "undated": undated,
                        "retained_alert": retained_alert,
                    },
                )
        except Exception:
            state_unavailable = True
    else:
        # Busy/unavailable locks reuse evidence with an explicit warning.
        state_unavailable = True

    notices = []
    health = "unknown"  # Absence of log errors never proves database integrity.
    if latest:
        recent = latest >= now - timedelta(hours=RECENT_ERROR_HOURS)
        health = "error" if recent else "unknown"
        notices.append(
            _notice(
                "index_corruption"
                if recent
                else (
                    "index_corruption_unconfirmed"
                    if retained_alert
                    else "index_corruption_history"
                ),
                "error" if recent else ("warning" if retained_alert else "info"),
                last_seen_at=latest.isoformat(),
            )
        )
    elif undated:
        notices.append(_notice("index_corruption_undated", "info"))
    if lease and (not readable or incomplete):
        notices.append(_notice("index_logs_unavailable", "info"))
    if pending or state_unavailable:
        notices.append(_notice("index_scan_incomplete", "warning"))
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
            response = pipeline.execute(raise_on_error=False)
        if len(response) != len(queues) + 1:
            raise ValueError("Invalid queue probe response")
        if isinstance(response[0], (redis.ConnectionError, redis.TimeoutError)):
            result["health_status"] = "error"
        elif response[0] is True:
            result["health_status"] = "ok"
        else:
            result["notices"].append(_notice("queue_probe_failed", "warning"))
        lengths = {}
        for queue, length in zip(queues, response[1:], strict=True):
            if isinstance(length, redis.ResponseError):
                result["notices"].append(
                    _notice("queue_metrics_unavailable", "warning", queue=queue)
                )
                continue
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
        if any(notice["code"] == "queue_backlog" for notice in result["notices"]):
            result["availability_status"] = "degraded"
    except (redis.ConnectionError, redis.TimeoutError, OSError):
        # Never return credentials, endpoints or Redis exception messages.
        result["health_status"] = "error"
        result["notices"] = [_notice("queue_probe_failed", "warning")]
    except (redis.RedisError, ValueError):
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
    key = "runtime:sl:v2:" + hashlib.sha256(config.encode()).hexdigest()
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
