"""Validated IP addresses for password-login Turnstile exemptions."""

from __future__ import annotations

from ipaddress import ip_address, ip_network
from typing import Any

from django.conf import settings

# Cloudflare's published reverse-proxy ranges: https://www.cloudflare.com/ips/
# Only a Cloudflare peer may supply CF-Connecting-IP. Unknown/new ranges fail
# closed until this list is updated; direct callers cannot forge that header.
_CLOUDFLARE_NETWORKS = tuple(
    ip_network(value)
    for value in (
        "173.245.48.0/20",
        "103.21.244.0/22",
        "103.22.200.0/22",
        "103.31.4.0/22",
        "141.101.64.0/18",
        "108.162.192.0/18",
        "190.93.240.0/20",
        "188.114.96.0/20",
        "197.234.240.0/22",
        "198.41.128.0/17",
        "162.158.0.0/15",
        "104.16.0.0/13",
        "104.24.0.0/14",
        "172.64.0.0/13",
        "131.0.72.0/22",
        "2400:cb00::/32",
        "2606:4700::/32",
        "2803:f800::/32",
        "2405:b500::/32",
        "2405:8100::/32",
        "2a06:98c0::/29",
        "2c0f:f248::/32",
    )
)


def normalize_ip(value: str) -> str:
    """Normalize a single IP, rejecting networks and scoped IPv6 addresses."""
    if "%" in value:
        raise ValueError("Scoped IPv6 addresses are not supported")
    address = ip_address(value)
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return str(address)


def normalize_allowlist(values: Any) -> list[str]:
    """Validate the entire list before saving, preserving first-seen order."""
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError("Enter a list of at most 100 individual IP addresses.")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError("Each allowlist entry must be an IP address.")
        try:
            normalized = normalize_ip(value.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid IP address: {value}") from exc
        if normalized not in result:
            result.append(normalized)
    return result


def login_client_ip(request: Any) -> str | None:
    """Resolve a client through the bundled gateway, not untrusted XFF prefixes.

    With TRUSTED_PROXY enabled, the backend must only be reachable via the
    bundled Nginx gateway, which appends its actual peer to X-Forwarded-For.
    Earlier values can be supplied by the client and must not grant exemptions.
    """
    meta = request.META
    if getattr(settings, "TRUSTED_PROXY", False):
        forwarded = str(meta.get("HTTP_X_FORWARDED_FOR", "") or "").strip()
        if not forwarded:
            return None
        value = forwarded.rsplit(",", 1)[-1].strip()
    else:
        value = str(meta.get("REMOTE_ADDR", "") or "").strip()
    try:
        peer = normalize_ip(value)
        if any(ip_address(peer) in network for network in _CLOUDFLARE_NETWORKS):
            # Cloudflare overwrites this header on the visitor-to-origin hop.
            return normalize_ip(str(meta.get("HTTP_CF_CONNECTING_IP", "")).strip())
        return peer
    except ValueError:
        return None


def login_ip_is_allowlisted(request: Any) -> bool:
    """Return whether this request has a configured password-login exemption."""
    from apps.configuration.services.runtime_settings import turnstile_ip_allowlist

    values = turnstile_ip_allowlist()
    if not values:
        return False
    client_ip = login_client_ip(request)
    if client_ip is None:
        return False
    try:
        return client_ip in normalize_allowlist(values)
    except ValueError:
        # Invalid persisted configuration never grants a bypass.
        return False
