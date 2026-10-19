"""Resolve the navigation-only product website URL for this instance."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings

from apps.configuration.constants import NOT_FOUND
from apps.configuration.models import GlobalConfig
from apps.configuration.selectors.interface import invalidate_config_cache
from apps.configuration.selectors.internal.resolver import resolve_global_value
from apps.instance_settings.conf import CONFIG_KEY_PRODUCT_WEBSITE_URL

if TYPE_CHECKING:
    from django.http import HttpRequest


def normalize_product_website_url(value: object) -> str:
    """Validate a browser HTTP(S) link without fetching its destination."""
    if not isinstance(value, str):
        raise ValueError("Product website URL must be a string.")
    raw = value.strip()
    if not raw:
        return ""
    if len(raw) > 2048 or re.search(r"\s|[\x00-\x1f\x7f\\]", raw):
        raise ValueError("Product website URL contains invalid characters or is too long.")
    try:
        raw.encode("utf-8")
        parsed = urlsplit(raw)
        port = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "%" in parsed.netloc
            or (port is not None and port < 1)
        ):
            raise ValueError
        hostname = parsed.hostname.lower()
        hostname = f"[{hostname}]" if ":" in hostname else hostname.encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        raise ValueError(
            "Product website URL must be a valid HTTP(S) URL without credentials."
        ) from None
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = hostname if port is None or port == default_port else f"{hostname}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path or "/", parsed.query, parsed.fragment))


def configured_product_website_url() -> str:
    """Read a valid instance-global override; ignore unsafe legacy values."""
    raw = resolve_global_value(config_key=CONFIG_KEY_PRODUCT_WEBSITE_URL)
    if raw is NOT_FOUND:
        return ""
    try:
        return normalize_product_website_url(raw)
    except ValueError:
        return ""


def default_product_website_url(request: HttpRequest) -> str:
    """Use the public request host and the published website port, never Agent URLs."""
    hostname = urlsplit(f"//{request.get_host()}").hostname
    if not hostname:
        return ""
    hostname = f"[{hostname}]" if ":" in hostname else hostname
    try:
        port = int(getattr(settings, "HFL_WEBSITE_PORT", 11442))
    except (TypeError, ValueError):
        port = 11442
    if not 1 <= port <= 65535:
        port = 11442
    netloc = hostname if port == 443 else f"{hostname}:{port}"
    return urlunsplit(("https", netloc, "", "", ""))


def effective_product_website_url(request: HttpRequest) -> str:
    """Resolve Runtime override > public-host default."""
    return configured_product_website_url() or default_product_website_url(request)


def set_product_website_url(value: object, *, user: Any = None) -> str:
    """Save an instance override; empty/null removes it rather than hiding the link."""
    normalized = "" if value is None else normalize_product_website_url(value)
    lookup = {
        "key": CONFIG_KEY_PRODUCT_WEBSITE_URL,
        "scope": GlobalConfig.Scope.GLOBAL,
        "tenant_key": "",
    }
    if not normalized:
        GlobalConfig.objects.filter(**lookup).delete()
    else:
        row, created = GlobalConfig.objects.update_or_create(
            **lookup,
            defaults={
                "value": normalized,
                "value_type": GlobalConfig.ValueType.STRING,
                "category": "deployment",
                "description": "Product website navigation URL",
                "is_active": True,
                "updated_by": user,
            },
        )
        if created and user is not None:
            row.created_by = user
            row.save(update_fields=["created_by"])
    invalidate_config_cache(key=CONFIG_KEY_PRODUCT_WEBSITE_URL, tenant_key="", scope="global")
    return normalized
