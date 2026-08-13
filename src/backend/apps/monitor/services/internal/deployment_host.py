"""Register and resolve control-plane deployment hosts."""

from __future__ import annotations

import os
import platform
import socket
from datetime import timedelta
from pathlib import Path

from django.db import transaction
from django.utils import timezone

from apps.monitor.models import DeploymentHost, SystemMetric

ONLINE_THRESHOLD = timedelta(minutes=5)
STALE_LIST_THRESHOLD = timedelta(hours=24)


def _looks_like_container_id(value: str) -> bool:
    value = value.strip()
    return len(value) == 12 and all(ch in "0123456789abcdef" for ch in value.lower())


def _read_machine_id() -> str | None:
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            machine_id = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if machine_id:
            return machine_id
    return None


def deployment_host_key() -> str:
    """Stable identity for one physical/virtual machine across containers."""
    machine_id = _read_machine_id()
    if machine_id:
        return machine_id[:255]

    boot = system_boot_time()
    if boot is not None:
        return f"host-{int(boot)}"[:255]

    return platform.node()[:255]


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


def _consolidate_duplicate_hosts(host: DeploymentHost) -> None:
    """Merge legacy per-container host rows that belong to the same machine."""
    if host.boot_time is None:
        return

    duplicates = DeploymentHost.objects.filter(boot_time=host.boot_time).exclude(pk=host.pk)
    if not duplicates.exists():
        return

    with transaction.atomic():
        for duplicate in duplicates:
            SystemMetric.objects.filter(host=duplicate).update(host=host)
        duplicates.delete()


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


def list_unique_deployment_hosts() -> list[DeploymentHost]:
    """Return one row per machine, preferring the most recently seen host."""
    hosts = list(DeploymentHost.objects.order_by("-last_seen_at", "hostname"))
    seen_boot: set[float] = set()
    unique: list[DeploymentHost] = []
    for host in hosts:
        if not is_visible_deployment_host(host):
            continue
        if host.boot_time is not None:
            if host.boot_time in seen_boot:
                continue
            seen_boot.add(host.boot_time)
        unique.append(host)
    return unique
