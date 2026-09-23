"""Bootstrap enrollment stub templates (deploy/bootstrap → API render)."""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings

from apps.node.services.internal.agent_release import (
    agent_releases_root,
    latest_published_agent_version,
)

_PLACEHOLDER_PREFIX = "__"
_PLACEHOLDER_SUFFIX = "__"

BOOTSTRAP_LINUX = "agent-bootstrap-linux.sh"
BOOTSTRAP_MACOS = "agent-bootstrap-macos.sh"
BOOTSTRAP_WINDOWS = "agent-bootstrap-windows.ps1"
BOOTSTRAP_GATEWAY_LINUX = "gateway-bootstrap-linux.sh"


def bootstrap_dir() -> Path:
    custom = (
        getattr(settings, "HFL_BOOTSTRAP_DIR", None)
        or os.getenv("HFL_BOOTSTRAP_DIR", "")
        or ""
    ).strip()
    if custom:
        return Path(custom)
    container_default = Path("/opt/hyperfilelens/bootstrap")
    if container_default.is_dir():
        return container_default
    repo_relative = Path(settings.BASE_DIR).resolve().parent.parent / "deploy" / "bootstrap"
    if repo_relative.is_dir():
        return repo_relative
    return container_default


def resolve_bootstrap_template_path(filename: str) -> Path:
    """
    Prefer published copy under media/agent-releases/<version>/, else deploy/bootstrap/.
    """
    version = os.getenv("AGENT_VERSION", "").strip()
    if not version:
        version = latest_published_agent_version()
    releases_path = agent_releases_root() / version / filename
    if releases_path.is_file():
        return releases_path
    source_path = bootstrap_dir() / filename
    if source_path.is_file():
        return source_path
    raise FileNotFoundError(
        f"bootstrap template missing: {filename} "
        f"(expected {releases_path} or {source_path}; run publish-agent.sh)",
    )


def render_bootstrap_script(filename: str, values: dict[str, str]) -> str:
    path = resolve_bootstrap_template_path(filename)
    text = path.read_text(encoding="utf-8")
    for key, val in values.items():
        token = f"{_PLACEHOLDER_PREFIX}{key}{_PLACEHOLDER_SUFFIX}"
        text = text.replace(token, val or "")
    return text
