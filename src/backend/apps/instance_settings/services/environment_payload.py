"""Environment / health snippets for instance settings (OSS).

Kept here so Community builds can show Admin Console environment without EE.
"""

from __future__ import annotations

import os
import time

from django.conf import settings
from django.db import connection
from django.utils import timezone

SCHEDULER_HEARTBEAT_KEY = "hyperfilelens:runtime:scheduler-heartbeat"


def probe_database() -> dict:
    started = timezone.now()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        latency_ms = int((timezone.now() - started).total_seconds() * 1000)
        db = settings.DATABASES.get("default", {})
        return {
            "status": "ok",
            "latency_ms": latency_ms,
            "engine": db.get("ENGINE", ""),
            "name": db.get("NAME", ""),
            "host": db.get("HOST", ""),
            "port": str(db.get("PORT", "")),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def probe_redis() -> dict:
    url = getattr(settings, "REDIS_URL", "") or os.getenv("REDIS_URL", "")
    if not url:
        return {"status": "unknown", "message": "REDIS_URL not configured"}
    try:
        import redis

        client = redis.from_url(url, socket_connect_timeout=2)
        started = timezone.now()
        client.ping()
        latency_ms = int((timezone.now() - started).total_seconds() * 1000)
        return {"status": "ok", "latency_ms": latency_ms}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def probe_celery() -> dict:
    worker_count = 0
    active_tasks = 0
    celery_status = "unknown"
    celery_error = None

    try:
        from celery import current_app

        inspect = current_app.control.inspect(timeout=2)
        stats = inspect.stats() or {}
        active = inspect.active() or {}
        celery_status = "ok" if stats else "degraded"
        worker_count = len(stats)
        active_tasks = sum(len(tasks or []) for tasks in active.values())
    except Exception as exc:
        celery_status = "error"
        celery_error = str(exc)

    from common.ops.runtime_backlog import runtime_backlog_snapshot

    backlog = runtime_backlog_snapshot()
    return {
        "status": celery_status,
        "worker_count": worker_count,
        "active_tasks": active_tasks,
        "error": celery_error,
        "backlog": backlog,
    }


def probe_scheduler() -> dict:
    url = getattr(settings, "REDIS_URL", "") or os.getenv("REDIS_URL", "")
    if not url:
        return {"status": "unknown", "message": "REDIS_URL not configured"}
    try:
        import redis

        client = redis.from_url(url, socket_connect_timeout=2, decode_responses=True)
        heartbeat = client.get(SCHEDULER_HEARTBEAT_KEY)
        if not heartbeat:
            return {"status": "degraded", "message": "Scheduler heartbeat not found"}
        age_seconds = max(0, int(time.time() - float(heartbeat)))
        return {
            "status": "ok" if age_seconds <= 30 else "degraded",
            "heartbeat_age_seconds": age_seconds,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def probe_web() -> dict:
    """Check the Web surfaces through the active blue/green gateway route."""
    import requests
    import urllib3

    try:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        # The release stack runs web-blue/web-green, while development runs web.
        # Nginx resolves the active Web pool in both modes.
        for url in (
            "https://nginx:11442/",
            "https://nginx:11443/",
            "https://nginx:11444/platform-ops/",
        ):
            response = requests.get(url, timeout=2, verify=False)
            # Tenant/Admin routes can reject an internal request without the
            # browser Host/auth context; 5xx still indicates a broken surface.
            if response.status_code >= 500:
                return {"status": "degraded", "message": "A Web frontend surface is unavailable"}
    except requests.RequestException:
        return {"status": "degraded", "message": "Web frontend is unreachable"}
    return {"status": "ok"}


def probe_nginx() -> dict:
    """Check the bundled TLS gateway listeners without requiring Docker access."""
    import socket

    try:
        for port in (11442, 11443, 11444):
            with socket.create_connection(("nginx", port), timeout=2):
                pass
    except OSError:
        return {"status": "degraded", "message": "Web gateway is unreachable"}
    return {"status": "ok"}


def probe_platform_data_gateway(*, source_lens_status: str = "") -> dict:
    from apps.lens_bridge.services.gateway_readiness import gateway_runtime_state
    from apps.lens_bridge.services.platform_lens import (
        resolve_platform_runtime_gateway_link,
    )

    link = resolve_platform_runtime_gateway_link()
    if link is None:
        return {
            "status": "not_configured",
            "configured": False,
            "deployment": "platform-managed",
            "agent_online": False,
            "lensnode_online": False,
            "control_plane_connected": False,
            "repository_access": False,
            "copilot_ready": False,
            "checked_at": timezone.now().isoformat(),
        }

    runtime = gateway_runtime_state(link, sl_runtime_status=source_lens_status)
    agent_online = bool(runtime["hfl_agent_online"])
    lensnode_online = bool(runtime["hfl_sidecar_online"])
    copilot_ready = bool(runtime["copilot_eligible"])
    return {
        "status": (
            "ok"
            if copilot_ready
            else "degraded"
        ),
        "configured": True,
        "deployment": "platform-managed",
        "name": link.gateway.name,
        "agent_online": agent_online,
        "lensnode_online": lensnode_online,
        "control_plane_connected": agent_online,
        "repository_access": bool(runtime["hfl_agent_capabilities_ready"]),
        "copilot_ready": copilot_ready,
        "agent_version": link.gateway.version or "",
        "sidecar_status": link.sidecar_status,
        "organization": link.organization.key if link.organization_id else "",
        "last_seen_at": (
            link.gateway.last_seen_at.isoformat()
            if link.gateway.last_seen_at
            else ""
        ),
        "checked_at": timezone.now().isoformat(),
    }


def system_health_payload() -> dict:
    return {
        "api": {"status": "ok"},
        "database": probe_database(),
        "redis": probe_redis(),
        "celery": probe_celery(),
        "scheduler": probe_scheduler(),
        "checked_at": timezone.now().isoformat(),
    }


def deploy_profile_staff_payload() -> dict:
    from apps.configuration.services.runtime_settings import (
        email_signup_enabled,
        password_reset_available,
        platform_ops_allowed_cidrs,
        platform_ops_enabled,
    )
    from common.deploy.site import tenant_public_url

    return {
        "platform_ops_enabled": platform_ops_enabled(),
        "email_signup_enabled": email_signup_enabled(),
        "password_reset_available": password_reset_available(),
        "tenant_public_url": tenant_public_url(),
        "platform_ops_allowed_cidrs": platform_ops_allowed_cidrs(),
        "app_version": (
            os.getenv("HFL_PRODUCT_VERSION", "").strip()
            or os.getenv("APP_VERSION", "").strip()
            or None
        ),
        "agent_version": os.getenv("AGENT_VERSION", "").strip() or None,
        "django_debug": bool(getattr(settings, "DEBUG", False)),
        "sentry_enabled": bool(getattr(settings, "SENTRY_ENABLED", False)),
        "sentry_environment": getattr(settings, "SENTRY_ENVIRONMENT", "") or None,
    }
