"""Privacy-safe, optional Sentry integration for HFL backend processes."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from common.observability.context import get_org_key, get_trace_id, get_user_id

logger = logging.getLogger(__name__)

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FILTERED = "[Filtered]"
_SAFE_CONTEXT_FIELDS = {
    "os": frozenset({"build", "kernel_version", "name", "version"}),
    "runtime": frozenset({"build", "name", "version"}),
    "trace": frozenset({"op", "origin", "parent_span_id", "span_id", "status", "trace_id"}),
}
_SAFE_SPAN_FIELDS = frozenset(
    {
        "op",
        "origin",
        "parent_span_id",
        "same_process_as_parent",
        "span_id",
        "start_timestamp",
        "status",
        "timestamp",
        "trace_id",
    }
)


def _env_bool(name: str, *, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in _TRUTHY


def _sample_rate(name: str, *, default: float = 0.0) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, min(1.0, value))


def _valid_dsn(value: str) -> bool:
    """Return whether *value* is a structurally valid HTTP(S) Sentry DSN."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    project_id = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and parsed.username
        and not parsed.query
        and not parsed.fragment
        and project_id.isdigit()
        and (port is None or 1 <= port <= 65535)
        and not re.search(r"\s", value)
    )


def _safe_url(value: Any) -> str | None:
    """Remove query, fragment, and resource paths from an event URL."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname
    if ":" in host:
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, "", "", ""))


def _pseudonym(value: str) -> str:
    """Return a deployment-scoped, irreversible identifier for Sentry tags."""
    secret = (os.getenv("SECRET_KEY") or "").encode("utf-8")
    if not value or not secret:
        return ""
    digest = hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:24]


def _before_breadcrumb(crumb: dict[str, Any], hint: Any) -> dict[str, Any]:  # noqa: ARG001
    """Retain breadcrumb classification while dropping user-controlled text."""
    return {
        key: crumb[key]
        for key in ("category", "level", "timestamp", "type")
        if key in crumb
    }


def _safe_spans(value: Any) -> list[dict[str, Any]]:
    """Retain trace topology and timing while dropping span payload content."""
    if not isinstance(value, list):
        return []
    spans: list[dict[str, Any]] = []
    for span in value:
        if not isinstance(span, Mapping):
            continue
        safe_span = {
            str(key): field
            for key, field in span.items()
            if str(key) in _SAFE_SPAN_FIELDS
            and isinstance(field, (bool, float, int, str))
        }
        if safe_span:
            spans.append(safe_span)
    return spans


def _before_send(event: dict[str, Any], hint: Any) -> dict[str, Any]:  # noqa: ARG001
    """Enrich an event with non-PII context and remove sensitive payloads."""
    event.pop("user", None)
    for key in ("breadcrumbs", "extra", "fingerprint", "logentry", "message"):
        event.pop(key, None)
    request = event.get("request")
    if isinstance(request, dict):
        safe_request: dict[str, Any] = {"headers": {}}
        if safe_request_url := _safe_url(request.get("url")):
            safe_request["url"] = safe_request_url
        event["request"] = safe_request

    transaction_info = event.get("transaction_info")
    route_transaction = (
        isinstance(transaction_info, Mapping)
        and transaction_info.get("source") == "route"
        and isinstance(event.get("transaction"), str)
    )
    if route_transaction:
        event["transaction_info"] = {"source": "route"}
    else:
        event.pop("transaction", None)
        event.pop("transaction_info", None)

    if spans := _safe_spans(event.get("spans")):
        event["spans"] = spans
    else:
        event.pop("spans", None)

    contexts = event.get("contexts")
    if isinstance(contexts, Mapping):
        event["contexts"] = {
            str(name): {
                str(key): value
                for key, value in context.items()
                if str(key) in _SAFE_CONTEXT_FIELDS[str(name)]
                and isinstance(value, (bool, float, int, str))
            }
            for name, context in contexts.items()
            if str(name) in _SAFE_CONTEXT_FIELDS and isinstance(context, Mapping)
        }
    else:
        event.pop("contexts", None)

    exception = event.get("exception")
    if isinstance(exception, dict):
        for value in exception.get("values") or []:
            if not isinstance(value, dict):
                continue
            if value.get("value"):
                value["value"] = _FILTERED
            mechanism = value.get("mechanism")
            if isinstance(mechanism, dict):
                mechanism.pop("data", None)
            stacktrace = value.get("stacktrace")
            if not isinstance(stacktrace, dict):
                continue
            for frame in stacktrace.get("frames") or []:
                if isinstance(frame, dict):
                    frame.pop("vars", None)

    tags: dict[str, str] = {}
    trace_id = get_trace_id()
    if trace_id:
        tags["trace_id"] = trace_id
    org_hash = _pseudonym(get_org_key())
    if org_hash:
        tags["org_hash"] = org_hash
    user_hash = _pseudonym(get_user_id())
    if user_hash:
        tags["user_hash"] = user_hash
    for env_name, tag_name in (
        ("SENTRY_COMPONENT", "component"),
        ("SENTRY_SERVICE", "service"),
        ("HFL_DEPLOY_TARGET", "deploy_target"),
        ("HFL_RELEASE_CHANNEL", "release_channel"),
    ):
        value = (os.getenv(env_name) or "").strip()
        if value:
            tags[tag_name] = value
    tags["product"] = "hyperfilelens"
    event["tags"] = tags
    return event


def init_sentry() -> None:
    """Initialize Sentry when enabled; configuration errors never block HFL."""
    if not _env_bool("SENTRY_ENABLED"):
        return

    dsn = (os.getenv("SENTRY_BACKEND_DSN") or "").strip()
    if not _valid_dsn(dsn):
        logger.warning("Sentry is enabled without a valid backend DSN; reporting is disabled")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
    except ImportError:
        logger.warning("sentry-sdk is unavailable; reporting is disabled")
        return

    environment = (os.getenv("SENTRY_ENVIRONMENT") or "").strip()
    release = (os.getenv("SENTRY_RELEASE") or os.getenv("APP_VERSION") or "").strip()
    try:
        sentry_sdk.init(
            dsn=dsn,
            integrations=[DjangoIntegration(), CeleryIntegration(), RedisIntegration()],
            environment=environment or None,
            release=release or None,
            traces_sample_rate=_sample_rate("SENTRY_TRACES_SAMPLE_RATE"),
            profiles_sample_rate=0.0,
            send_default_pii=False,
            include_local_variables=False,
            max_request_body_size="never",
            before_breadcrumb=_before_breadcrumb,
            before_send=_before_send,
            before_send_transaction=_before_send,
        )
    except Exception as exc:  # Sentry must never prevent process startup.
        logger.warning("Sentry initialization failed; reporting is disabled: %s", exc)
