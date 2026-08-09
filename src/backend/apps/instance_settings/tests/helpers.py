"""Shared helpers for instance-settings API tests."""

from __future__ import annotations

from typing import Any
from unittest import SkipTest


def ensure_ops_staff_role(user: Any) -> None:
    """Attach PlatformAdmin when EE AuthZ is loaded (no-op on Community)."""
    try:
        from apps.membership.testing import ensure_platform_role

        ensure_platform_role(user)
    except Exception:
        pass


def skip_if_extensions_loaded(reason: str = "Requires Community empty socket") -> None:
    """Skip empty-socket assertions when the platform extension is mounted."""
    try:
        from common.extension_loader import extensions_enabled

        if extensions_enabled():
            raise SkipTest(reason)
    except SkipTest:
        raise
    except Exception:
        pass
