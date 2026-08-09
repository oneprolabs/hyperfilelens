from __future__ import annotations

import ipaddress
import re


_DNS_HOST_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.?$"
)


def normalize_repository_server_host(value: object) -> str:
    host = str(value or "").strip()
    if not host:
        return ""
    if "://" in host or "/" in host or any(char.isspace() for char in host):
        raise ValueError("Enter an IPv4, IPv6, or DNS host without a scheme, path, or port.")
    unbracketed = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    try:
        return ipaddress.ip_address(unbracketed).compressed
    except ValueError:
        pass
    if ":" in host or not _DNS_HOST_RE.fullmatch(host):
        raise ValueError("Enter a valid IPv4, IPv6, or DNS host without a port.")
    return host.rstrip(".").lower()


__all__ = ["normalize_repository_server_host"]
