"""Identity/session restrictions shared by authenticated API entry points."""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import PermissionDenied

from apps.iam.constants import SUPPORT_SESSION_KEY
from apps.iam.services.account_setup import assert_setup_request_allowed


def assert_authenticated_request_allowed(request: Any, user: Any) -> None:
    """No authority source may bypass setup or an explicit read-only session."""
    assert_setup_request_allowed(request, user)
    # Account/session operations and the Admin Console are separate surfaces.
    if (
        user.is_staff and request.method not in ("GET", "HEAD", "OPTIONS")
        and request.path.startswith("/api/v1/")
        and not request.path.startswith(("/api/v1/auth/", "/api/v1/platform-ops/"))
    ):
        org_key = request.headers.get("X-Org-Key") or request.query_params.get("org")
        if org_key and getattr(request, "session", {}).get(SUPPORT_SESSION_KEY) == org_key:
            raise PermissionDenied("Support mode is read-only.")
