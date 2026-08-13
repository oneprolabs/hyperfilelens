"""Liveness and readiness health check views for orchestrators."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any

from django.db import connection
from django.http import JsonResponse
from django.utils import timezone


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def _check_db() -> CheckResult:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return CheckResult("database", True)
    except Exception as exc:
        return CheckResult("database", False, str(exc)[:200])


def _check_cache() -> CheckResult:
    try:
        from django.core.cache import cache

        key = "healthcheck:ping"
        cache.set(key, "1", 5)
        ok = cache.get(key) == "1"
        return CheckResult("cache", bool(ok), "" if ok else "unexpected cache value")
    except Exception as exc:
        return CheckResult("cache", False, str(exc)[:200])


def readiness(_request) -> JsonResponse:
    results = [_check_db(), _check_cache()]
    ok = all(r.ok for r in results)
    payload: dict[str, Any] = {
        "ok": ok,
        "time": timezone.now().isoformat(),
        "version": (
            str(os.getenv("HFL_PRODUCT_VERSION", "")).strip()
            or str(os.getenv("APP_VERSION", "")).strip()
            or "0.0.0"
        ),
        "checks": [asdict(r) for r in results],
    }
    return JsonResponse(payload, status=200 if ok else 503)


def liveness(_request) -> JsonResponse:
    return JsonResponse(
        {
            "ok": True,
            "time": timezone.now().isoformat(),
        },
        status=200,
    )
