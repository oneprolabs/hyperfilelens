"""Optional Turnstile enforcement for anonymous authentication endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.iam.services.turnstile_allowlist import login_ip_is_allowlisted
from apps.iam.services.turnstile_service import get_client_ip, validate_turnstile
from common.deploy.site import resolve_site_role

TURNSTILE_FIELD = "turnstile_token"


def turnstile_enabled() -> bool:
    """Return whether this deployment requires Turnstile verification."""
    return bool(getattr(settings, "TURNSTILE_ENABLED", False))


def turnstile_configured() -> bool:
    """Return whether both effective Turnstile keys are available."""
    from apps.configuration.services.runtime_settings import (
        turnstile_secret_key,
        turnstile_site_key,
    )

    return bool(turnstile_site_key() and turnstile_secret_key())


def turnstile_required(request: Any, *, action: str = "") -> bool:
    """Require tenant verification, except allowlisted password logins."""
    from apps.configuration.services.runtime_settings import enterprise_identity_enabled

    if not enterprise_identity_enabled():
        return False
    if not turnstile_enabled() or resolve_site_role(request) != "tenant":
        return False
    return not (action == "login" and login_ip_is_allowlisted(request))


def missing_turnstile_fields(
    data: Mapping[str, Any],
    request: Any,
    *,
    action: str = "",
) -> dict[str, list[str]]:
    """Return required-field errors when this request requires Turnstile."""
    if not turnstile_required(request, action=action) or str(
        data.get(TURNSTILE_FIELD) or ""
    ).strip():
        return {}
    return {TURNSTILE_FIELD: [_("Required")]}


def credentials_and_turnstile_present(
    data: Mapping[str, Any],
    credential_fields: list[str],
    request: Any,
    *,
    action: str = "",
) -> bool:
    """Return whether credentials and any required Turnstile token are present."""
    if any(not data.get(field) for field in credential_fields):
        return False
    return not missing_turnstile_fields(data, request, action=action)


def expected_turnstile_hostname(request) -> str:
    """Resolve the hostname expected in Cloudflare's Siteverify response."""
    frontend_hostname = urlparse(str(getattr(settings, "FRONTEND_URL", ""))).hostname
    if frontend_hostname:
        return frontend_hostname.lower()
    return request.get_host().split(":", 1)[0].lower()


def verify_turnstile_for_action(
    data: Mapping[str, Any],
    request,
    *,
    action: str,
) -> bool:
    """Verify Turnstile for an action, or allow it when explicitly disabled."""
    if not turnstile_required(request, action=action):
        return True
    if not turnstile_configured():
        return False

    token = str(data.get(TURNSTILE_FIELD) or "").strip()
    if not token:
        return False
    return validate_turnstile(
        token,
        get_client_ip(request),
        expected_action=action,
        expected_hostname=expected_turnstile_hostname(request),
    )


def invalid_turnstile_fields() -> dict[str, list[str]]:
    """Return a consistent validation error for rejected Turnstile tokens."""
    return {TURNSTILE_FIELD: [_("Invalid or expired human verification")]}
