"""First-login restriction shared by authentication and account services."""

from __future__ import annotations

from typing import Any

from django.utils.translation import gettext as _

from rest_framework.exceptions import PermissionDenied

from apps.iam.profile_models import Profile

SETUP_ENDPOINTS = {
    ("GET", "/api/v1/auth/user"),
    ("POST", "/api/v1/auth/change-password"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/logout"),
    ("GET", "/api/v1/auth/token/refresh"),
    ("POST", "/api/v1/auth/token/refresh"),
}


def requires_password_change(user: Any) -> bool:
    return bool(
        user
        and user.is_authenticated
        and Profile.objects.filter(
            user_id=user.pk,
            requires_password_change=True,
        ).exists()
    )


def assert_account_ready(user: Any) -> None:
    if requires_password_change(user):
        raise PermissionDenied(
            {
                "error_code": "PASSWORD_SETUP_REQUIRED",
                "message": _("Set your own password before using this account."),
            },
            code="password_setup_required",
        )


def assert_setup_request_allowed(request: Any, user: Any) -> None:
    if (request.method, request.path.rstrip("/")) not in SETUP_ENDPOINTS:
        assert_account_ready(user)
