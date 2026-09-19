"""Register and resolve control-plane deployment hosts."""

from __future__ import annotations

import os
import platform
import re
import socket
import uuid
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.monitor.models import DeploymentHost, SystemMetric

ONLINE_THRESHOLD = timedelta(minutes=5)
STALE_LIST_THRESHOLD = timedelta(hours=24)
_BOOT_DERIVED_HOSTNAME = re.compile(r"^host-\d+$")


def _looks_like_container_id(value: str) -> bool:
    value = value.strip()
    return len(value) == 12 and all(ch in "0123456789abcdef" for ch in value.lower())


def _is_boot_derived_hostname(hostname: str) -> bool:
    return bool(_BOOT_DERIVED_HOSTNAME.fullmatch((hostname or "").strip()))


def _read_machine_id() -> str | None:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            machine_id = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if machine_id:
            return machine_id
    return None


def _env_deployment_host_key() -> str | None:
    value = (os.getenv("HFL_DEPLOYMENT_HOST_KEY") or "").strip()
    return value[:255] if value else None


def _persisted_deployment_host_key() -> str | None:
    """Stable id shared by api/worker via MEDIA_ROOT when machine-id is missing."""
    try:
        from django.conf import settings

        root = Path(settings.MEDIA_ROOT)
    except Exception:
        return None

    path = root / ".hfl_deployment_host_id"
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value[:255]
        root.mkdir(parents=True, exist_ok=True)
        value = uuid.uuid4().hex
        tmp = path.with_suffix(".tmp")
        tmp.write_text(value + "\n", encoding="utf-8")
        tmp.replace(path)
        return value
    except OSError:
        return None


def deployment_host_key() -> str:
    """Stable identity for one physical/virtual machine across containers.

    Do not key on ``boot_time``: in Docker/WSL it can drift and previously
    created a new DeploymentHost row on every metrics sample.
    """
    machine_id = _read_machine_id()
    if machine_id:
        return machine_id[:255]

    env_key = _env_deployment_host_key()
    if env_key:
        return env_key

    persisted = _persisted_deployment_host_key()
    if persisted:
        return persisted

    node = platform.node().strip()
    if node:
        return f"node-{node}"[:255]
    return "deployment-host"


def deployment_host_display_name() -> str:
    node = platform.node().strip()
    if node and not _looks_like_container_id(node):
        return node[:255]

    for candidate in (os.getenv("HOSTNAME", "").strip(), socket.gethostname().strip()):
        if candidate and not _looks_like_container_id(candidate):
            return candidate[:255]

    return node[:255] if node else deployment_host_key()[:255]


def _primary_ip() -> str | None:
    try:
        import psutil
    except ImportError:
        return None

    for name, addrs in psutil.net_if_addrs().items():
        if name.startswith(("lo", "docker", "br-", "veth")):
            continue
        for addr in addrs:
            if addr.family == socket.AF_INET and not addr.address.startswith("127."):
                return addr.address
    return None


def system_boot_time() -> float | None:
    try:
        import psutil
    except ImportError:
        return None
    return float(psutil.boot_time())


def current_host_identity() -> dict:
    return {
        "hostname": deployment_host_key(),
        "name": deployment_host_display_name(),
        "platform": platform.platform(),
        "python_version": platform.python_version(),
        "ip_address": _primary_ip(),
        "app_version": (
            os.getenv("HFL_PRODUCT_VERSION", "").strip()
            or os.getenv("APP_VERSION", "").strip()
        ),
        "boot_time": system_boot_time(),
    }


CONSOLIDATE_BATCH_SIZE = 100


def _duplicate_host_query(host: DeploymentHost) -> Q | None:
    query = Q()
    has_clause = False
    if host.boot_time is not None:
        query |= Q(boot_time=host.boot_time)
        has_clause = True
    if host.name:
        # Legacy rows keyed as host-{boot} for the same display name.
        query |= Q(name=host.name, hostname__regex=r"^host-[0-9]+$")
        has_clause = True
        if host.ip_address:
            query |= Q(name=host.name, ip_address=host.ip_address)
    return query if has_clause else None


def _consolidate_duplicate_hosts(host: DeploymentHost) -> None:
    """Merge a bounded batch of legacy rows so one sample cannot lock the DB."""
    query = _duplicate_host_query(host)
    if query is None:
        return

    duplicate_ids = list(
        DeploymentHost.objects.filter(query)
        .exclude(pk=host.pk)
        .order_by("last_seen_at", "hostname")
        .values_list("pk", flat=True)[:CONSOLIDATE_BATCH_SIZE]
    )
    if not duplicate_ids:
        return

    with transaction.atomic():
        SystemMetric.objects.filter(host_id__in=duplicate_ids).update(host=host)
        DeploymentHost.objects.filter(pk__in=duplicate_ids).delete()


def touch_local_deployment_host() -> DeploymentHost:
    """Create or update the host record for the machine running this process."""
    identity = current_host_identity()
    host, _created = DeploymentHost.objects.get_or_create(
        hostname=identity["hostname"],
        defaults={
            "name": identity["name"],
            "platform": identity["platform"],
            "python_version": identity["python_version"],
            "ip_address": identity["ip_address"],
            "app_version": identity["app_version"],
            "boot_time": identity["boot_time"],
            "last_seen_at": timezone.now(),
        },
    )

    changed: list[str] = []
    for field, key in (
        ("name", "name"),
        ("platform", "platform"),
        ("python_version", "python_version"),
        ("ip_address", "ip_address"),
        ("app_version", "app_version"),
        ("boot_time", "boot_time"),
    ):
        value = identity[key]
        if getattr(host, field) != value:
            setattr(host, field, value)
            changed.append(field)
    host.last_seen_at = timezone.now()
    changed.append("last_seen_at")
    host.save(update_fields=changed)

    _consolidate_duplicate_hosts(host)
    return host


def is_host_online(host: DeploymentHost) -> bool:
    if not host.last_seen_at:
        return False
    return timezone.now() - host.last_seen_at <= ONLINE_THRESHOLD


def is_visible_deployment_host(host: DeploymentHost) -> bool:
    """Hide long-offline stale rows (e.g. old dev runs outside Docker)."""
    if is_host_online(host):
        return True
    if not host.last_seen_at:
        return False
    return timezone.now() - host.last_seen_at <= STALE_LIST_THRESHOLD


def host_to_dict(host: DeploymentHost) -> dict:
    return {
        "id": str(host.id),
        "hostname": host.hostname,
        "name": host.name or host.hostname,
        "ip_address": host.ip_address or "",
        "platform": host.platform,
        "python_version": host.python_version,
        "app_version": host.app_version,
        "status": "online" if is_host_online(host) else "offline",
        "last_seen_at": host.last_seen_at.isoformat() if host.last_seen_at else None,
        "boot_time": host.boot_time,
    }


def host_to_monitor_dict(host: DeploymentHost) -> dict:
    payload = host_to_dict(host)
    payload["python"] = host.python_version
    return payload


def _host_list_identity(host: DeploymentHost) -> str:
    """Collapse rows that represent the same machine in the picker."""
    if host.name:
        return f"name:{host.name}"
    if host.hostname and not _is_boot_derived_hostname(host.hostname):
        return f"key:{host.hostname}"
    if host.boot_time is not None:
        return f"boot:{host.boot_time}"
    return f"id:{host.id}"


def list_unique_deployment_hosts() -> list[DeploymentHost]:
    """Return one row per machine, preferring the most recently seen host."""
    cutoff = timezone.now() - STALE_LIST_THRESHOLD
    hosts = list(
        DeploymentHost.objects.filter(last_seen_at__gte=cutoff).order_by(
            "-last_seen_at",
            "hostname",
        )
    )
    seen: set[str] = set()
    unique: list[DeploymentHost] = []
    for host in hosts:
        if not is_visible_deployment_host(host):
            continue
        identity = _host_list_identity(host)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(host)
    return unique
