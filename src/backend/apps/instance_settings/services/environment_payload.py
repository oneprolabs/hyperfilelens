"""Environment / health snippets for instance settings (OSS).

Kept here so Community builds can show Admin Console environment without EE.
"""

from __future__ import annotations

import os

from django.conf import settings
from django.db import connection
from django.utils import timezone


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
    if celery_status == "ok" and backlog["status"] != "ok":
        celery_status = "degraded"
    return {
        "status": celery_status,
        "worker_count": worker_count,
        "active_tasks": active_tasks,
        "error": celery_error,
        "backlog": backlog,
    }


def system_health_payload() -> dict:
    return {
        "api": {"status": "ok"},
        "database": probe_database(),
        "redis": probe_redis(),
        "celery": probe_celery(),
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
