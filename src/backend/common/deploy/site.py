"""Resolve fixed tenant/operations access from the trusted edge proxy."""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings

if TYPE_CHECKING:
    from django.http import HttpRequest


# Community Host socket: AI Models (instance essential).
PLATFORM_OPS_LANDING_PATH_COMMUNITY = "/platform-ops/engine/ai-settings"
# Enterprise plugin adds Overview; prefer it when extensions are loaded.
PLATFORM_OPS_LANDING_PATH_ENTERPRISE = "/platform-ops/overview"
# Back-compat alias (enterprise default); prefer platform_ops_landing_path().
PLATFORM_OPS_LANDING_PATH = PLATFORM_OPS_LANDING_PATH_ENTERPRISE


def platform_ops_landing_path() -> str:
    """Ops-site home: overview with plugin, AI Models on empty socket."""
    try:
        from common.extension_loader import extensions_enabled

        if extensions_enabled():
            return PLATFORM_OPS_LANDING_PATH_ENTERPRISE
    except Exception:  # pragma: no cover
        pass
    return PLATFORM_OPS_LANDING_PATH_COMMUNITY


def platform_ops_landing_path_for_user(user) -> str:
    """Role-aware Admin Console landing (Platform AuthZ).

    Billing → Quota Usage; Infra → AI Models; Admin / unknown → default.
    """
    try:
        from common.platform_authz import (
            ROLE_BILLING_OPERATOR,
            ROLE_INFRA_OPERATOR,
            get_platform_role,
        )

        role = get_platform_role(user)
        if role == ROLE_BILLING_OPERATOR:
            return "/platform-ops/platform/quota-usage"
        if role == ROLE_INFRA_OPERATOR:
            return "/platform-ops/engine/ai-settings"
    except Exception:  # pragma: no cover
        pass
    return platform_ops_landing_path()


def resolve_site_role(request: HttpRequest) -> str:
    """Return ``tenant`` or ``ops`` from the edge-controlled request header."""
    role = str(request.META.get("HTTP_X_HFL_SITE_ROLE") or "").strip().lower()
    return "ops" if role == "ops" else "tenant"


def _client_ip(request: HttpRequest) -> str | None:
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").strip()
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def client_ip_allowed(request: HttpRequest, cidrs: list[str]) -> bool:
    if not cidrs:
        return True
    client = _client_ip(request)
    if not client:
        return False
    try:
        ip = ipaddress.ip_address(client)
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        if ip in network:
            return True
    return False


def platform_ops_api_allowed(request: HttpRequest) -> bool:
    """Whether Platform Ops API may be used on this request."""
    from apps.configuration.services.runtime_settings import (
        platform_ops_allowed_cidrs,
        platform_ops_enabled,
    )
    from common.platform_authz import get_platform_role

    if not platform_ops_enabled():
        return False
    user = request.user
    if not user or not user.is_authenticated or not user.is_staff:
        return False
    # EE: require explicit platform role. Community (no AuthzProvider): staff OK.
    if get_platform_role(user) is None:
        return False

    if resolve_site_role(request) != "ops":
        return False

    return client_ip_allowed(
        request,
        platform_ops_allowed_cidrs(),
    )


def platform_ops_access_allowed(request: HttpRequest) -> bool:
    """Whether the current operations-site user may open Platform Ops."""
    from apps.configuration.services.runtime_settings import platform_ops_enabled
    from common.platform_authz import get_platform_role

    if not platform_ops_enabled():
        return False
    user = request.user
    if not user or not user.is_authenticated or not user.is_staff:
        return False
    if get_platform_role(user) is None:
        return False

    return resolve_site_role(request) == "ops"


def admin_console_entry_visible(request: HttpRequest) -> bool:
    """Whether the tenant shell should show the Admin Console entry."""
    from apps.configuration.services.runtime_settings import platform_ops_enabled
    from common.platform_authz import get_platform_role

    user = request.user
    return bool(
        platform_ops_enabled()
        and resolve_site_role(request) == "tenant"
        and user
        and user.is_authenticated
        and user.is_staff
        and get_platform_role(user) is not None
    )


def tenant_public_url() -> str:
    return str(getattr(settings, "FRONTEND_URL", "")).strip().rstrip("/")


def enrollment_tls_verify() -> bool:
    """Return whether generated Agent enrollment clients must verify TLS."""
    return not bool(getattr(settings, "HFL_INSECURE_TLS", True))


def admin_console_public_url(request: HttpRequest) -> str:
    """Build the Admin Console origin from the current host and configured port."""
    configured = str(getattr(settings, "HFL_ADMIN_PUBLIC_URL", "")).strip()
    if configured:
        return configured.rstrip("/")

    parsed_host = urlsplit(f"//{request.get_host()}")
    if not parsed_host.hostname:
        return ""

    hostname = parsed_host.hostname
    if ":" in hostname:
        hostname = f"[{hostname}]"

    forwarded_proto = str(request.META.get("HTTP_X_FORWARDED_PROTO") or "")
    scheme = forwarded_proto.split(",")[0].strip() or request.scheme or "https"
    port = int(getattr(settings, "HFL_ADMIN_PORT", 11444))
    default_port = 443 if scheme == "https" else 80
    netloc = hostname if port == default_port else f"{hostname}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def default_landing_path(request: HttpRequest) -> str:
    site = resolve_site_role(request)
    if site == "ops" and platform_ops_access_allowed(request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return platform_ops_landing_path_for_user(user)
        return platform_ops_landing_path()
    return "/"
