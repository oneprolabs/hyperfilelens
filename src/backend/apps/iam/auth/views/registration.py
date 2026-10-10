"""
Registration and password reset API views.
"""

import logging
import re

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import logout as django_logout
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from common.http.public_api import AnonymousPublicViewMixin

from apps.iam.profile_models import Profile
from apps.iam.services.turnstile_verification import (
    invalid_turnstile_fields,
    missing_turnstile_fields,
    turnstile_configured,
    turnstile_required,
    verify_turnstile_for_action,
)
from apps.iam.services.verification_code_service import verify_email_verification_code
from apps.iam.services.registration_service import (
    complete_user_registration,
    generate_password_reset_code,
    generate_registration_verification_code,
    mask_email,
    validate_password_format,
)
from apps.iam.services.token_service import blacklist_all_user_tokens
from apps.iam.services.email_code_login_service import (
    CODE_MAX_ATTEMPTS,
    check_send_rate_limit,
    check_verify_rate_limit,
)
from apps.iam.services.turnstile_service import get_client_ip
from apps.configuration.services import runtime_settings as runtime_settings_svc
from apps.configuration.services.runtime_settings import (
    email_delivery_configured,
    email_signup_enabled,
)

User = get_user_model()
logger = logging.getLogger(__name__)

DEBUG_MODE = getattr(settings, 'DEBUG', False)


def _reset_rate_error(rate):
    if rate.allowed:
        return None
    response = _build_error_response(
        "PASSWORD_RESET_RATE_LIMITED",
        _("Too many verification-code requests. Please try again later."),
        http_status=status.HTTP_429_TOO_MANY_REQUESTS,
    )
    response["Retry-After"] = str(max(1, rate.retry_after))
    return response


def _unique_email_user(email, *, lock=False):
    queryset = User.objects.select_for_update() if lock else User.objects.all()
    users = list(queryset.filter(email__iexact=email).order_by("id")[:2])
    return users[0] if len(users) == 1 else None


def _save_password_and_revoke(user, password):
    """Caller holds the user lock and transaction covering code consumption."""
    from apps.iam.email_verification_models import EmailVerificationCode

    user.set_password(password)
    user.save(update_fields=["password"])
    EmailVerificationCode.objects.filter(user=user, is_used=False).update(
        is_used=True, used_at=timezone.now(), invalidated_at=timezone.now(),
    )
    # A cache failure raises and rolls back the password/code database changes.
    blacklist_all_user_tokens(user.id, "password_changed")


def _clear_password_auth(request, response):
    cookie_settings = getattr(settings, "COOKIE_AUTH", {})
    response.delete_cookie(cookie_settings.get("ACCESS_TOKEN_COOKIE_NAME", "access_token"), path="/")
    response.delete_cookie(
        cookie_settings.get("REFRESH_TOKEN_COOKIE_NAME", "refresh_token"),
        path="/api/v1/auth/token/refresh",
    )
    response.delete_cookie("token_family", path="/")
    django_logout(request)
    request.session.flush()
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    return response


def _build_error_response(
    code: str,
    message: str,
    fields: dict | None = None,
    http_status: int = status.HTTP_400_BAD_REQUEST,
) -> Response:
    data = {"error_code": code, "message": message}
    if fields:
        data["fields"] = fields
    return Response(
        {
            "code": "1001",
            "error": data,
        },
        status=http_status,
    )


def _validate_email_format(email: str) -> bool:
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email))


def _email_signup_disabled_response() -> Response:
    """Return the canonical response for disabled public email sign-up."""
    return _build_error_response(
        "EMAIL_SIGNUP_DISABLED",
        _("Email sign-up is disabled"),
        http_status=status.HTTP_403_FORBIDDEN,
    )


def _password_reset_disabled_response() -> Response:
    """Self-serve password reset is an enterprise identity feature."""
    return _build_error_response(
        "PASSWORD_RESET_DISABLED",
        _("Password reset is disabled"),
        http_status=status.HTTP_403_FORBIDDEN,
    )


def _email_service_unavailable_response() -> Response:
    return _build_error_response(
        "EMAIL_SERVICE_UNAVAILABLE",
        _("Email service is temporarily unavailable"),
        http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _turnstile_misconfigured_response(request: Request) -> Response | None:
    """Fail closed when this request requires Turnstile without complete keys."""
    if not turnstile_required(request) or turnstile_configured():
        return None
    return _build_error_response(
        "TURNSTILE_MISCONFIGURED",
        _("Human verification is temporarily unavailable"),
        http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


def _turnstile_invalid_response() -> Response:
    """Return the canonical response for a rejected Turnstile token."""
    return _build_error_response(
        "TURNSTILE_INVALID",
        _("Invalid or expired human verification"),
        fields=invalid_turnstile_fields(),
    )


def _unique_username_for_email(email: str) -> str:
    username_base = email.split("@")[0]
    username = username_base
    counter = 1
    while User.objects.filter(username=username).exists():
        username = f"{username_base}{counter}"
        counter += 1
    return username


def _pending_registration_user(user: User) -> bool:
    try:
        profile = Profile.objects.get(user=user)
    except Profile.DoesNotExist:
        return not user.is_active
    return not user.is_active and not profile.registration_completed


def _forgot_password_generic_success(email: str) -> Response:
    return Response(
        {
            "code": "0000",
            "data": {
                "message": _("If an account exists, a reset code has been sent"),
                "email": mask_email(email),
            },
        },
        status=status.HTTP_200_OK,
    )

def _get_or_create_pending_user(email: str) -> tuple[User | None, str | None, bool]:
    """
    Return an inactive pending user for registration, or an error message.
    The third value indicates whether a new user was created.
    """
    existing = User.objects.filter(email=email).first()
    if existing is not None:
        if existing.is_active:
            return None, "EMAIL_EXISTS", False
        return existing, None, False

    user = User.objects.create(
        username=_unique_username_for_email(email),
        email=email,
        is_active=False,
    )
    try:
        profile = Profile.objects.get(user=user)
        profile.registration_completed = False
        profile.save(update_fields=["registration_completed"])
    except Profile.DoesNotExist:
        Profile.objects.create(user=user, registration_completed=False)
    return user, None, True


class EmailRegisterSendCodeView(AnonymousPublicViewMixin, APIView):
    """
    Send a 6-digit registration verification code to the user's email.
    POST /api/v1/auth/email-register/send-code
    """

    @extend_schema(
        tags=["auth"],
        summary="Send registration verification code",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "User email"},
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
        if not email_signup_enabled():
            return _email_signup_disabled_response()
        if not email_delivery_configured():
            return _email_service_unavailable_response()

        configuration_error = _turnstile_misconfigured_response(request)
        if configuration_error is not None:
            return configuration_error

        email = (request.data.get("email") or "").strip().lower()

        if not email or missing_turnstile_fields(request.data, request):
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "VALIDATION_ERROR",
                        "message": _("Missing required fields"),
                        "fields": {
                            **missing_turnstile_fields(request.data, request),
                            "email": [_('Required')] if not email else [],
                        },
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not verify_turnstile_for_action(
            request.data,
            request,
            action="register_send_code",
        ):
            return _turnstile_invalid_response()

        if not _validate_email_format(email):
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "INVALID_EMAIL",
                        "message": _("Invalid email format"),
                        "fields": {"email": [_('Invalid email format')]},
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user, error_code, created = _get_or_create_pending_user(email)
        if error_code == "EMAIL_EXISTS":
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "EMAIL_EXISTS",
                        "message": _("This email is already registered"),
                        "fields": {"email": [_('This email is already registered')]},
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        code, error = generate_registration_verification_code(user)
        if error:
            if created:
                user.delete()
            return _email_service_unavailable_response()

        response_data = {
            "code": "0000",
            "data": {
                "message": _("Verification code sent to your email"),
                "email": mask_email(email),
            },
        }

        if DEBUG_MODE:
            response_data["data"]["_debug_code"] = code
            logger.info(f"[DEBUG] Registration code for {email}: {code}")

        return Response(response_data, status=status.HTTP_200_OK)


class EmailRegisterView(AnonymousPublicViewMixin, APIView):
    """
    Register a new user with email verification.
    POST /api/v1/auth/email-register

    Sends a 6-digit verification code to the user's email.
    """

    @extend_schema(
        tags=["auth"],
        summary="Register new user",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "first_name": {"type": "string", "description": "First name"},
                    "last_name": {"type": "string", "description": "Last name"},
                    "company_name": {"type": "string", "description": "Company name"},
                    "country": {"type": "string", "description": "Country code"},
                    "email": {"type": "string", "description": "User email"},
                    "turnstile_token": {
                        "type": "string",
                        "description": "Required when Turnstile is enabled",
                    },
                },
                "required": ["first_name", "last_name", "email"],
            }
        },
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        if not email_signup_enabled():
            return _email_signup_disabled_response()
        if not email_delivery_configured():
            return _email_service_unavailable_response()

        configuration_error = _turnstile_misconfigured_response(request)
        if configuration_error is not None:
            return configuration_error

        first_name = request.data.get("first_name")
        last_name = request.data.get("last_name")
        email = request.data.get("email")

        if not all([first_name, last_name, email]) or missing_turnstile_fields(
            request.data,
            request,
        ):
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "VALIDATION_ERROR",
                        "message": _("Missing required fields"),
                        "fields": {
                            **missing_turnstile_fields(request.data, request),
                            "first_name": [_('Required')] if not first_name else [],
                            "last_name": [_('Required')] if not last_name else [],
                            "email": [_('Required')] if not email else [],
                        },
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not verify_turnstile_for_action(request.data, request, action="register"):
            return _turnstile_invalid_response()

        if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "INVALID_EMAIL",
                        "message": _("Invalid email format"),
                        "fields": {"email": [_('Invalid email format')]},
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if User.objects.filter(email=email).exists():
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "EMAIL_EXISTS",
                        "message": _("This email is already registered"),
                        "fields": {"email": [_('This email is already registered')]},
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        username_base = email.split("@")[0]
        username = username_base
        counter = 1
        while User.objects.filter(username=username).exists():
            username = f"{username_base}{counter}"
            counter += 1

        user = User.objects.create(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            is_active=False,
        )

        try:
            profile = Profile.objects.get(user=user)
            profile.registration_completed = False
            profile.save(update_fields=["registration_completed"])
        except Profile.DoesNotExist:
            Profile.objects.create(
                user=user,
                registration_completed=False,
            )

        code, error = generate_registration_verification_code(user)

        if error:
            user.delete()
            return _email_service_unavailable_response()

        response_data = {
            "code": "0000",
            "data": {
                "message": _("Verification code sent to your email"),
                "email": mask_email(email),
            },
        }

        # DEBUG: Return code in response so it can be seen in browser console
        if DEBUG_MODE:
            response_data["data"]["_debug_code"] = code
            logger.info(f"[DEBUG] Registration code for {email}: {code}")

        return Response(response_data, status=status.HTTP_200_OK)


class EmailRegisterConfirmView(AnonymousPublicViewMixin, APIView):
    """
    Confirm registration with verification code.
    POST /api/v1/auth/email-register/confirm
    """

    @extend_schema(
        tags=["auth"],
        summary="Confirm registration",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "User email"},
                    "code": {"type": "string", "description": "6-digit verification code"},
                    "password": {"type": "string", "description": "Password (8-20 chars, must contain uppercase, lowercase and digits)"},
                },
                "required": ["email", "code", "password"],
            }
        },
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        if not email_signup_enabled():
            return _email_signup_disabled_response()

        email = (request.data.get("email") or "").strip().lower()
        code = request.data.get("code")
        password = request.data.get("password")

        if not all([email, code, password]):
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "VALIDATION_ERROR",
                        "message": _("Missing required fields"),
                        "fields": {
                            "email": [_('Required')] if not email else [],
                            "code": [_('Required')] if not code else [],
                            "password": [_('Required')] if not password else [],
                        },
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_valid, error_msg = validate_password_format(password)
        if not is_valid:
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "INVALID_PASSWORD",
                        "message": error_msg,
                        "fields": {"password": [error_msg]},
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = User.objects.filter(email=email).first()
        if user is None:
            msg = _("No account found with this email")
            return _build_error_response(
                "USER_NOT_FOUND",
                msg,
                fields={"email": [msg]},
                http_status=status.HTTP_404_NOT_FOUND,
            )

        if user.is_active:
            msg = _("Account is already active")
            return _build_error_response(
                "ALREADY_ACTIVE",
                msg,
                fields={"email": [msg]},
            )

        success, error_reason, org = complete_user_registration(user, password, code)
        if not success:
            if error_reason == "ALREADY_ACTIVE":
                msg = _("Account is already active")
                return _build_error_response(
                    "ALREADY_ACTIVE",
                    msg,
                    fields={"email": [msg]},
                )
            msg = _("Invalid or expired verification code")
            return _build_error_response(
                "INVALID_CODE",
                msg,
                fields={"code": [msg]},
            )

        return Response(
            {
                "code": "0000",
                "data": {
                    "message": _("Registration completed successfully"),
                    "user": {
                        "id": user.id,
                        "email": user.email,
                        "username": user.username,
                    },
                    "organization": {
                        "key": org.key,
                        "name": org.name,
                    },
                },
            },
            status=status.HTTP_200_OK,
        )


class ForgotPasswordView(AnonymousPublicViewMixin, APIView):
    """
    Request password reset email.
    POST /api/v1/auth/forgot-password

    Sends a 6-digit reset code to the user's email.
    """

    @extend_schema(
        tags=["auth"],
        summary="Request password reset",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "User email"},
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
        # Community empty socket: self-serve reset is EE-only.
        if not runtime_settings_svc.enterprise_identity_enabled():
            return _password_reset_disabled_response()

        configuration_error = _turnstile_misconfigured_response(request)
        if configuration_error is not None:
            return configuration_error

        email = (request.data.get("email") or "").strip().lower()

        if not email or missing_turnstile_fields(request.data, request):
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "VALIDATION_ERROR",
                        "message": _("Missing required fields"),
                        "fields": {
                            **missing_turnstile_fields(request.data, request),                            "email": [_('Required')] if not email else [],
                        },
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not verify_turnstile_for_action(
            request.data,
            request,
            action="forgot_password",
        ):
            return _turnstile_invalid_response()

        if not email_delivery_configured():
            return _email_service_unavailable_response()

        rate_error = _reset_rate_error(check_send_rate_limit(
            email=email, client_ip=get_client_ip(request) or "", purpose="password_reset",
        ))
        if rate_error is not None:
            return rate_error

        user = _unique_email_user(email)

        if user is None:
            msg = _("This email is not registered")
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "EMAIL_NOT_REGISTERED",
                        "message": msg,
                        "fields": {"email": [msg]},
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.is_active:
            if email_signup_enabled() and _pending_registration_user(user):
                code, error = generate_registration_verification_code(user)
                if error:
                    return _email_service_unavailable_response()
                response_data = {
                    "code": "0000",
                    "data": {
                        "message": _(
                            "This account has not completed registration. "
                            "A verification code has been sent to your email."
                        ),
                        "email": mask_email(email),
                        "pending_registration": True,
                    },
                }
                if DEBUG_MODE:
                    response_data["data"]["_debug_code"] = code
                    logger.info(f"[DEBUG] Registration code for pending user {email}: {code}")
                return Response(response_data, status=status.HTTP_200_OK)
            return _forgot_password_generic_success(email)

        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=user.pk)
            if not user.is_active or user.email.lower() != email:
                return _forgot_password_generic_success(email)
            code, error = generate_password_reset_code(user)

        if error:
            return _email_service_unavailable_response()

        response_data = {
            "code": "0000",
            "data": {
                "message": _("Reset code sent to your email"),
                "email": mask_email(email),
            },
        }

        return Response(response_data, status=status.HTTP_200_OK)


class ForgotPasswordConfirmView(AnonymousPublicViewMixin, APIView):
    """
    Reset password with verification code.
    POST /api/v1/auth/forgot-password/confirm
    """

    @extend_schema(
        tags=["auth"],
        summary="Reset password with code",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "User email"},
                    "code": {"type": "string", "description": "6-digit reset code"},
                    "password": {"type": "string", "description": "New password (8-20 chars, must contain uppercase, lowercase and digits)"},
                },
                "required": ["email", "code", "password"],
            }
        },
        responses={200: OpenApiTypes.OBJECT},
    )
    @transaction.atomic
    def post(self, request):
        if not runtime_settings_svc.enterprise_identity_enabled():
            return _password_reset_disabled_response()

        email = (request.data.get("email") or "").strip().lower()
        code = request.data.get("code")
        password = request.data.get("password")

        if not all([email, code, password]):
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "VALIDATION_ERROR",
                        "message": _("Missing required fields"),
                        "fields": {
                            "email": [_('Required')] if not email else [],
                            "code": [_('Required')] if not code else [],
                            "password": [_('Required')] if not password else [],
                        },
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        is_valid, error_msg = validate_password_format(password)
        if not is_valid:
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "INVALID_PASSWORD",
                        "message": error_msg,
                        "fields": {"password": [error_msg]},
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        rate_error = _reset_rate_error(check_verify_rate_limit(
            email=email, client_ip=get_client_ip(request) or "", purpose="password_reset",
        ))
        if rate_error is not None:
            return rate_error

        user = _unique_email_user(email, lock=True)
        if user is None or not user.is_active:
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "USER_NOT_FOUND",
                        "message": _("No active account found with this email"),
                    },
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        from apps.iam.email_verification_models import EmailVerificationCode

        is_valid, error_reason = verify_email_verification_code(
            user,
            code,
            purpose=EmailVerificationCode.Purpose.PASSWORD_RESET,
            max_attempts=CODE_MAX_ATTEMPTS,
            allow_legacy=False,
            recipient_email=user.email,
        )

        if not is_valid:
            return Response(
                {
                    "code": "1001",
                    "error": {
                        "error_code": "INVALID_CODE",
                        "message": _("Invalid or expired reset code"),
                        "reason": error_reason,
                    },
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        _save_password_and_revoke(user, password)

        return _clear_password_auth(request, Response(
            {
                "code": "0000",
                "data": {
                    "requires_relogin": True,
                    "message": _("Password has been reset successfully"),
                },
            },
            status=status.HTTP_200_OK,
        ))


class ChangePasswordView(APIView):
    """
    Change password for the currently authenticated user.
    POST /api/v1/auth/change-password
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["auth"],
        summary="Change password",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "current_password": {"type": "string", "description": "Current password"},
                    "new_password": {"type": "string", "description": "New password (8-20 chars, must contain uppercase, lowercase and digits)"},
                    "confirm_password": {"type": "string", "description": "New password confirmation"},
                },
                "required": ["current_password", "new_password", "confirm_password"],
            }
        },
        responses={200: OpenApiTypes.OBJECT},
    )
    @transaction.atomic
    def post(self, request):
        auth_error = getattr(request, "auth_error", None)
        if auth_error:
            error_code = auth_error.get("error_code", "OTHER_DEVICE_LOGIN")
            message = auth_error.get("message", _("Your account was logged in from another device"))
            raise AuthenticationFailed(
                detail={"error_code": error_code, "message": message},
                code=error_code.lower(),
            )

        user = User.objects.select_for_update().get(pk=request.user.pk)
        current_password = request.data.get("current_password") or ""
        new_password = request.data.get("new_password") or ""
        confirm_password = request.data.get("confirm_password") or ""

        fields = {}
        if not current_password:
            fields["current_password"] = [_('Please enter current password')]
        if not new_password:
            fields["new_password"] = [_('Please enter new password')]
        if not confirm_password:
            fields["confirm_password"] = [_('Please confirm new password')]
        if fields:
            return _build_error_response(
                "VALIDATION_ERROR",
                _("Password validation failed"),
                fields=fields,
            )

        if not user.check_password(current_password):
            return _build_error_response(
                "CURRENT_PASSWORD_INCORRECT",
                _("Current password is incorrect"),
                fields={
                    "current_password": [
                        _("Current password is incorrect"),
                    ],
                },
            )

        is_valid, error_msg = validate_password_format(new_password)
        if not is_valid:
            message = error_msg or _("Invalid password")
            return _build_error_response(
                "INVALID_PASSWORD",
                message,
                fields={"new_password": [message]},
            )

        if new_password != confirm_password:
            return _build_error_response(
                "PASSWORD_MISMATCH",
                _("New passwords do not match"),
                fields={
                    "confirm_password": [
                        _("New passwords do not match"),
                    ],
                },
            )

        _save_password_and_revoke(user, new_password)

        response = Response(
            {
                "code": "0000",
                "data": {
                    "requires_relogin": True,
                    "message": _("Password changed. Please sign in again."),
                },
            },
            status=status.HTTP_200_OK,
        )
        return _clear_password_auth(request, response)
