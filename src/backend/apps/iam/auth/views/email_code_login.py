"""Tenant email verification-code login endpoints."""

from __future__ import annotations

import logging
import time

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.config import get_login_verification_code_minutes
from apps.iam.models import Membership
from apps.iam.services.email_code_login_service import (
    SEND_COOLDOWN_SECONDS,
    check_send_rate_limit,
    check_verify_rate_limit,
    issue_login_code,
    verify_login_code,
)
from apps.iam.services.turnstile_service import get_client_ip
from apps.iam.services.turnstile_verification import (
    invalid_turnstile_fields,
    missing_turnstile_fields,
    turnstile_configured,
    turnstile_required,
    verify_turnstile_for_action,
)
from apps.configuration.services.runtime_settings import (
    email_code_login_enabled,
    email_delivery_configured,
)
from common.deploy.site import resolve_site_role
from common.http.public_api import AnonymousPublicViewMixin

User = get_user_model()
logger = logging.getLogger(__name__)

GENERIC_SEND_MESSAGE = _(
    "Request received. Please check your email for the verification code. "
    "If it doesn't arrive, confirm that the email address is correct."
)


def _error_response(
    error_code: str,
    message: str,
    *,
    http_status: int = status.HTTP_400_BAD_REQUEST,
    fields: dict | None = None,
    retry_after: int = 0,
) -> Response:
    error = {"error_code": error_code, "message": message}
    if fields:
        error["fields"] = fields
    if retry_after:
        error["retry_after"] = retry_after
    response = Response(
        {"code": "1001", "error": error},
        status=http_status,
    )
    if retry_after:
        response["Retry-After"] = str(retry_after)
    return response


def _feature_unavailable_response(request) -> Response | None:
    if resolve_site_role(request) != "tenant" or not email_code_login_enabled():
        return _error_response(
            "EMAIL_CODE_LOGIN_DISABLED",
            _("Email verification-code sign-in is not enabled"),
            http_status=status.HTTP_403_FORBIDDEN,
        )
    if not email_delivery_configured():
        return _error_response(
            "EMAIL_SERVICE_UNAVAILABLE",
            _("Email service is temporarily unavailable"),
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return None


def _normalized_email(raw: object) -> str:
    return str(raw or "").strip().lower()


def _valid_email(email: str) -> bool:
    try:
        validate_email(email)
    except ValidationError:
        return False
    return True


def _eligible_user(email: str):
    users = list(
        User.objects.filter(email__iexact=email).order_by("id")[:2]
    )
    if len(users) != 1:
        return None

    user = users[0]
    if not user.is_active or user.is_staff:
        return None
    if not Membership.objects.filter(
        user=user,
        is_active=True,
        organization__is_active=True,
    ).exists():
        return None
    return user


def _rate_limited_response(retry_after: int) -> Response:
    return _error_response(
        "EMAIL_CODE_RATE_LIMITED",
        _("Too many verification-code requests. Please try again later."),
        http_status=status.HTTP_429_TOO_MANY_REQUESTS,
        retry_after=max(1, retry_after),
    )


class EmailCodeLoginSendView(AnonymousPublicViewMixin, APIView):
    """Send a purpose-bound sign-in code with deployment-controlled Turnstile."""

    @extend_schema(
        tags=["auth"],
        summary="Send email sign-in code",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "turnstile_token": {
                        "type": "string",
                        "description": "Required when Turnstile is enabled",
                    },
                },
                "required": ["email"],
            }
        },
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        unavailable = _feature_unavailable_response(request)
        if unavailable is not None:
            return unavailable

        email = _normalized_email(request.data.get("email"))
        if not _valid_email(email):
            return _error_response(
                "INVALID_EMAIL",
                _("Invalid email format"),
                fields={"email": [_('Invalid email format')]},
            )

        if turnstile_required(request) and not turnstile_configured():
            return _error_response(
                "TURNSTILE_MISCONFIGURED",
                _("Human verification is temporarily unavailable"),
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        missing = missing_turnstile_fields(request.data, request)
        if missing:
            return _error_response(
                "VALIDATION_ERROR",
                _("Missing required fields"),
                fields=missing,
            )
        if not verify_turnstile_for_action(
            request.data, request, action="email_login_send_code"
        ):
            return _error_response(
                "TURNSTILE_INVALID",
                _("Invalid or expired human verification"),
                fields=invalid_turnstile_fields(),
            )

        rate = check_send_rate_limit(
            email=email,
            client_ip=get_client_ip(request) or "",
        )
        if not rate.allowed:
            return _rate_limited_response(rate.retry_after)

        user = _eligible_user(email)
        expires_in = get_login_verification_code_minutes() * 60
        if user is not None:
            try:
                expires_in = issue_login_code(user)
            except Exception as exc:
                logger.warning(
                    "email sign-in code delivery failed user_id=%s: %s",
                    user.id,
                    type(exc).__name__,
                )
                return _error_response(
                    "EMAIL_SERVICE_UNAVAILABLE",
                    _("Email service is temporarily unavailable"),
                    http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        return Response(
            {
                "code": "0000",
                "data": {
                    "message": GENERIC_SEND_MESSAGE,
                    "retry_after": SEND_COOLDOWN_SECONDS,
                    "expires_in": expires_in,
                },
            },
            status=status.HTTP_200_OK,
        )


class EmailCodeLoginVerifyView(AnonymousPublicViewMixin, APIView):
    """Verify a sign-in code and hand off to the existing org selection flow."""

    @extend_schema(
        tags=["auth"],
        summary="Sign in with email verification code",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "email": {"type": "string"},
                    "code": {"type": "string"},
                },
                "required": ["email", "code"],
            }
        },
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        unavailable = _feature_unavailable_response(request)
        if unavailable is not None:
            return unavailable

        email = _normalized_email(request.data.get("email"))
        code = str(request.data.get("code") or "").strip()
        if not _valid_email(email) or len(code) != 6 or not code.isdigit():
            return _error_response(
                "VALIDATION_ERROR",
                _("Enter a valid email address and 6-digit verification code"),
                fields={
                    "email": [_('Invalid email format')] if not _valid_email(email) else [],
                    "code": [_('Enter a 6-digit verification code')]
                    if len(code) != 6 or not code.isdigit()
                    else [],
                },
            )

        rate = check_verify_rate_limit(
            email=email,
            client_ip=get_client_ip(request) or "",
        )
        if not rate.allowed:
            return _rate_limited_response(rate.retry_after)

        user = _eligible_user(email)
        if user is None:
            return _error_response(
                "INVALID_OR_EXPIRED_CODE",
                _("Invalid or expired verification code"),
                fields={"code": [_('Invalid or expired verification code')]},
            )

        valid, reason = verify_login_code(user, code)
        if not valid:
            error_code = (
                "EMAIL_CODE_ATTEMPTS_EXCEEDED"
                if reason == "TOO_MANY_ATTEMPTS"
                else "INVALID_OR_EXPIRED_CODE"
            )
            message = (
                _("Too many incorrect attempts. Request a new verification code.")
                if reason == "TOO_MANY_ATTEMPTS"
                else _("Invalid or expired verification code")
            )
            return _error_response(
                error_code,
                message,
                fields={"code": [message]},
            )

        memberships = list(
            Membership.objects.filter(
                user=user,
                is_active=True,
                organization__is_active=True,
            ).select_related("organization")
        )
        if not memberships:
            return _error_response(
                "ACCOUNT_ACCESS_UNAVAILABLE",
                _("No active organization is available for this account"),
                http_status=status.HTTP_403_FORBIDDEN,
            )

        request.session["pending_user_id"] = user.id
        request.session["pending_password_fingerprint"] = user.get_session_auth_hash()
        request.session["pending_login_at"] = time.time()
        request.session.save()
        from apps.iam.services.membership_service import authoritative_role

        available_orgs = [
            {
                "org_key": membership.organization.key,
                "org_name": membership.organization.name,
                "role": authoritative_role(membership),
            }
            for membership in memberships
        ]
        return Response(
            {
                "code": "0000",
                "data": {
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "username": user.username,
                        "is_staff": False,
                    },
                    "roles": [authoritative_role(membership) for membership in memberships],
                    "available_orgs": available_orgs,
                    "message": _("Select organization to continue"),
                },
            },
            status=status.HTTP_200_OK,
        )
