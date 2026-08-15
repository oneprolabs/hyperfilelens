"""Resolve the public product identity exposed to HyperFileLens clients."""

from __future__ import annotations

import os
import re

COMMUNITY_EDITION = "community"
ENTERPRISE_EDITION = "enterprise"
SUPPORTED_EDITIONS = frozenset({COMMUNITY_EDITION, ENTERPRISE_EDITION})

_PRODUCT_VERSION_RE = re.compile(
    r"^v?(?P<version>\d+\.\d+\.\d+(?:[-+][0-9A-Za-z][0-9A-Za-z.-]*)?)$"
)


def product_version() -> str | None:
    """Return a customer-facing release version without image-only suffixes."""
    raw = os.getenv("HFL_PRODUCT_VERSION", "").strip()
    if not raw:
        # Older installations may only expose APP_VERSION. Enterprise image
        # tags append ``-ee``; that suffix is not part of the product version.
        raw = os.getenv("APP_VERSION", "").strip()
        if raw.endswith("-ee"):
            raw = raw[:-3]

    match = _PRODUCT_VERSION_RE.fullmatch(raw)
    return match.group("version") if match else None


def product_edition() -> str:
    """Return the supported public edition, with a safe source-build fallback."""
    configured = os.getenv("HFL_EDITION", "").strip().lower()
    try:
        from common.extension_loader import extensions_enabled

        if extensions_enabled():
            return ENTERPRISE_EDITION
    except Exception:  # pragma: no cover - startup fallback must stay safe
        pass

    return configured if configured in SUPPORTED_EDITIONS else COMMUNITY_EDITION
