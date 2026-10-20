"""Strict POSIX path validation for Data Gateway filesystem boundaries."""

from __future__ import annotations

import posixpath

PLATFORM_WORKSPACE_ROOT = "/opt/hyperfilelens-agent/workspace/platform/data"


class GatewayPathError(ValueError):
    """Raised when a gateway path can escape its declared filesystem root."""


def normalize_absolute_posix_path(path: str, *, field: str = "path") -> str:
    """Return a canonical absolute POSIX path without ambiguous components."""

    raw = str(path or "").strip()
    if not raw:
        raise GatewayPathError(f"{field} is required")
    if "\x00" in raw or "\\" in raw:
        raise GatewayPathError(f"{field} contains an unsupported character")
    if not raw.startswith("/"):
        raise GatewayPathError(f"{field} must be an absolute POSIX path")
    parts = raw.split("/")
    if any(part in {".", ".."} for part in parts):
        raise GatewayPathError(f"{field} contains an unsafe path component")
    normalized = posixpath.normpath(raw)
    if not normalized.startswith("/"):
        raise GatewayPathError(f"{field} must be an absolute POSIX path")
    return normalized


def path_within_root(
    path: str,
    root: str,
    *,
    allow_root: bool,
    field: str = "path",
) -> str:
    """Validate and return ``path`` contained by ``root`` using components."""

    normalized_root = normalize_absolute_posix_path(root, field="root")
    normalized_path = normalize_absolute_posix_path(path, field=field)
    try:
        common = posixpath.commonpath((normalized_root, normalized_path))
    except ValueError as exc:
        raise GatewayPathError(f"{field} is outside the gateway root") from exc
    if common != normalized_root or (
        normalized_path == normalized_root and not allow_root
    ):
        relation = "at or under" if allow_root else "under"
        raise GatewayPathError(f"{field} must be {relation} the gateway root")
    return normalized_path
