"""Community external-access URL configuration."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings

from apps.configuration.models import GlobalConfig
from apps.configuration.selectors.interface import get_config, invalidate_config_cache
from apps.instance_settings.conf import CONFIG_KEY_EXTERNAL_ACCESS_URL
from common.deploy.product import COMMUNITY_EDITION, product_edition

if TYPE_CHECKING:
    from django.http import HttpRequest


def normalize_external_access_url(value: object) -> str:
    """Return a canonical HTTP(S) origin or raise ``ValueError``."""
    if not isinstance(value, str):
        raise ValueError("External access URL must be a string.")
    raw = value.strip()
    if not raw:
        return ""
    if len(raw) > 2048:
        raise ValueError("External access URL must not exceed 2048 characters.")
    if re.search(r"\s", raw) or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise ValueError(
            "External access URL must not contain whitespace or control characters."
        )

    parsed = urlsplit(raw)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("External access URL contains an invalid port.") from exc
    if "\\" in raw or "%" in parsed.netloc:
        raise ValueError("External access URL contains an invalid hostname.")
    if port is not None and port < 1:
        raise ValueError("External access URL contains an invalid port.")
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "External access URL must be an HTTP(S) origin without credentials, "
            "a path, query, or fragment."
        )
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()
    if ":" in hostname:
        hostname = f"[{hostname}]"
    default_port = 443 if scheme == "https" else 80
    netloc = hostname if port is None or port == default_port else f"{hostname}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def configured_external_access_url() -> str:
    """Return the valid administrator override, if one exists."""
    value = get_config(CONFIG_KEY_EXTERNAL_ACCESS_URL, default="")
    try:
        normalized = normalize_external_access_url(value)
    except ValueError:
        return ""
    if (
        normalized
        and not bool(getattr(settings, "HFL_INSECURE_TLS", True))
        and urlsplit(normalized).scheme != "https"
    ):
        return ""
    return normalized


def effective_external_access_url() -> str:
    """Resolve Community override, then the deployment-provided URL."""
    if product_edition() == COMMUNITY_EDITION:
        configured = configured_external_access_url()
        if configured:
            return configured
    return str(getattr(settings, "FRONTEND_URL", "")).strip().rstrip("/")


def external_access_source() -> str:
    """Describe the source used by ``effective_external_access_url``."""
    if (
        product_edition() == COMMUNITY_EDITION
        and configured_external_access_url()
    ):
        return "runtime"
    return "deployment"


def suggested_external_access_url(request: HttpRequest) -> str:
    """Suggest the tenant origin corresponding to the current Admin request."""
    parsed_host = urlsplit(f"//{request.get_host()}")
    if not parsed_host.hostname:
        return ""
    hostname = parsed_host.hostname
    if ":" in hostname:
        hostname = f"[{hostname}]"
    forwarded_proto = str(request.META.get("HTTP_X_FORWARDED_PROTO") or "")
    scheme = forwarded_proto.split(",")[0].strip() or request.scheme or "https"
    if scheme not in {"http", "https"}:
        scheme = request.scheme if request.scheme in {"http", "https"} else "https"
    port = int(getattr(settings, "HFL_TENANT_PORT", 11443))
    if port < 1 or port > 65535:
        port = 11443
    default_port = 443 if scheme == "https" else 80
    netloc = hostname if port == default_port else f"{hostname}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def set_external_access_url(value: object, *, user=None) -> str:
    """Set or clear the Community external-access URL override."""
    if product_edition() != COMMUNITY_EDITION:
        raise ValueError(
            "External access is managed by Enterprise deployment configuration."
        )
    normalized = normalize_external_access_url(value)
    if (
        normalized
        and not bool(getattr(settings, "HFL_INSECURE_TLS", True))
        and urlsplit(normalized).scheme != "https"
    ):
        raise ValueError(
            "External access URL must use HTTPS when TLS verification is enabled."
        )
    if normalized:
        row, created = GlobalConfig.objects.update_or_create(
            key=CONFIG_KEY_EXTERNAL_ACCESS_URL,
            scope=GlobalConfig.Scope.GLOBAL,
            tenant_key="",
            defaults={
                "value": normalized,
                "value_type": GlobalConfig.ValueType.STRING,
                "category": "deployment",
                "description": "Community external access URL",
                "is_active": True,
                "updated_by": user,
            },
        )
        if created and user is not None:
            row.created_by = user
            row.save(update_fields=["created_by"])
    else:
        GlobalConfig.objects.filter(
            key=CONFIG_KEY_EXTERNAL_ACCESS_URL,
            scope=GlobalConfig.Scope.GLOBAL,
            tenant_key="",
        ).delete()
    invalidate_config_cache(
        key=CONFIG_KEY_EXTERNAL_ACCESS_URL,
        tenant_key="",
        scope="global",
    )
    return normalized
