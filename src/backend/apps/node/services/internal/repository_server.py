from __future__ import annotations

import ipaddress
import re


_DNS_HOST_RE = re.compile(
    r"^(?=.{1,253}\.?$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.?$"
)

_REPOSITORY_SERVER_ERROR_MESSAGES = {
    "REPOSITORY_SERVER_TLS_PREPARE_FAILED": (
        "The storage Proxy could not prepare TLS for the temporary Repository Server. "
        "Upgrade or repair the Proxy Agent, then retry."
    ),
    "REPOSITORY_SERVER_PROCESS_START_FAILED": (
        "The temporary Repository Server could not start on the storage Proxy. Retry the task."
    ),
    "REPOSITORY_SERVER_PROCESS_EXITED": (
        "The temporary Repository Server stopped before it became ready. Retry the task."
    ),
    "REPOSITORY_SERVER_READY_TIMEOUT": (
        "The storage Proxy did not make the Repository Server ready in time. "
        "Check Proxy resources and retry."
    ),
    "REPOSITORY_SERVER_START_CANCELED": "Repository Server startup was canceled.",
    "REPOSITORY_SERVER_SESSION_CONFLICT": (
        "Another Repository Server session is using the requested resources. Retry the task."
    ),
    "REPOSITORY_SERVER_PORT_UNAVAILABLE": (
        "No temporary Repository Server port is available on the storage Proxy. Retry the task."
    ),
    "REPOSITORY_SERVER_SESSION_STATE_FAILED": (
        "The storage Proxy could not persist the Repository Server session. Retry the task."
    ),
    "REPOSITORY_SERVER_STOP_FAILED": (
        "The storage Proxy could not stop the temporary Repository Server. Retry cleanup."
    ),
    "REPOSITORY_SERVER_AGENT_UPGRADE_REQUIRED": (
        "The storage Proxy Agent uses the legacy Repository Server startup mechanism. "
        "Upgrade the Proxy Agent, then retry."
    ),
}


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


def repository_server_diagnostic_code(
    result: object,
    message: object = "",
) -> str:
    payload = result if isinstance(result, dict) else {}
    code = str(payload.get("error_code") or "").strip().upper()
    if code in _REPOSITORY_SERVER_ERROR_MESSAGES:
        return code
    text = str(message or "").strip()
    message_code = text.partition(":")[0].strip().upper()
    if message_code in _REPOSITORY_SERVER_ERROR_MESSAGES:
        return message_code
    if "no available repository server port" in text.lower():
        return "REPOSITORY_SERVER_PORT_UNAVAILABLE"
    if "did not create tls certificate within" in text.lower():
        return "REPOSITORY_SERVER_AGENT_UPGRADE_REQUIRED"
    return ""


def repository_server_public_error_message(code: object) -> str:
    return _REPOSITORY_SERVER_ERROR_MESSAGES.get(str(code or "").strip().upper(), "")


__all__ = [
    "normalize_repository_server_host",
    "repository_server_diagnostic_code",
    "repository_server_public_error_message",
]
