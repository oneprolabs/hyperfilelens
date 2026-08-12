"""Trusted observability policy for platform-owned Data Gateways."""

from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings

from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY
from apps.node.models import Node
from apps.node.models.base import NodeRole

_ENVIRONMENT_RE = re.compile(r"^hfl-(test|community|preprod|production)$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


def _valid_public_dsn(value: str) -> bool:
    """Accept only public-key HTTP(S) DSNs safe to distribute to platform hosts."""
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
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and project_id.isdigit()
        and (port is None or 1 <= port <= 65535)
        and not re.search(r"\s", value)
    )


def _safe_version(value: str, *, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip()
    return normalized if _VERSION_RE.fullmatch(normalized) else fallback


def is_platform_gateway(node: Node) -> bool:
    """Return whether *node* is a trusted platform-owned Gateway."""
    if (
        node.role != NodeRole.GATEWAY
        or node.organization.key != PLATFORM_ORG_KEY
        or node.is_deleted
    ):
        return False
    return LensGatewayLink.objects.filter(
        organization=node.organization,
        gateway=node,
        scope=LensGatewayLink.GatewayScope.PLATFORM,
        is_deleted=False,
    ).exists()


def gateway_observability_policy(node: Node) -> dict[str, Any]:
    """Return a bounded Sentry policy; private Gateways are always disabled."""
    disabled: dict[str, Any] = {"enabled": False}
    if not is_platform_gateway(node) or not bool(settings.SENTRY_ENABLED):
        return disabled

    dsn = str(os.getenv("SENTRY_BACKEND_DSN") or "").strip()
    environment = str(getattr(settings, "SENTRY_ENVIRONMENT", "") or "").strip()
    if not _valid_public_dsn(dsn) or not _ENVIRONMENT_RE.fullmatch(environment):
        return disabled

    agent_version = _safe_version(node.version or os.getenv("AGENT_VERSION", ""))
    sourcelens_version = _safe_version(
        str(os.getenv("SOURCELENS_GIT_REF") or "").strip().removeprefix("v")
    )
    return {
        "enabled": True,
        "backend_dsn": dsn,
        "environment": environment,
        "agent_release": f"hyperfilelens-agent@{agent_version}",
        "lensnode_release": (
            f"hyperfilelens-lensnode@{agent_version}-sl{sourcelens_version}"
        ),
        # Error tracking is enabled; performance tracing remains opt-in.
        "traces_sample_rate": 0.0,
        "send_default_pii": False,
    }
