"""JWT authentication from HttpOnly cookies with IAM token lifecycle checks."""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractBaseUser
from rest_framework import exceptions
from rest_framework.request import Request
from rest_framework_simplejwt.authentication import (
    JWTAuthentication as DefaultJWTAuthentication,
)
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import Token

from apps.iam.services.token_service import (
    TokenError as TokenErrorCode,
    get_user_token_invalid_reason,
    get_token_error_message,
    is_token_blacklisted,
    is_token_version_valid,
)

logger = logging.getLogger(__name__)

User = get_user_model()

COOKIE_ACCESS_TOKEN_NAME = "access_token"
COOKIE_REFRESH_TOKEN_NAME = "refresh_token"


def get_token_from_cookie(request: Request, token_name: str) -> str | None:
    """Extract token from HttpOnly cookie."""
    return request.COOKIES.get(token_name)


class JWTAuthenticationFromCookies(DefaultJWTAuthentication):
    """JWT auth: cookie first, then Authorization header; blacklist + token version."""

    def _raise_token_lifecycle_error(self, error_code: str) -> None:
        raise exceptions.AuthenticationFailed(
            {
                "error_code": error_code,
                "message": get_token_error_message(error_code),
            },
            code=error_code.lower(),
        )

    def authenticate(
        self, request: Request,
    ) -> tuple[AbstractBaseUser, Token] | None:
        from apps.iam.services.request_access import assert_authenticated_request_allowed

        result = self._authenticate(request)
        if result is not None:
            assert_authenticated_request_allowed(request, result[0])
        return result

    def _authenticate(
        self,
        request: Request,
    ) -> tuple[AbstractBaseUser, Token] | None:
        raw_token = get_token_from_cookie(request, COOKIE_ACCESS_TOKEN_NAME)

        if raw_token is None:
            try:
                result = super().authenticate(request)
            except (InvalidToken, TokenError) as exc:
                self._raise_mapped_token_error(exc)
            if result is None:
                return None
            user, validated_token = result
            if not self._check_blacklist(validated_token):
                return None
            token_version = validated_token.get("token_version")
            if token_version is not None:
                user_id = validated_token.get("user_id")
                if not is_token_version_valid(user_id, token_version):
                    error_code = (
                        TokenErrorCode.PASSWORD_CHANGED
                        if get_user_token_invalid_reason(user_id) == "password_changed"
                        else TokenErrorCode.OTHER_DEVICE_LOGIN
                    )
                    logger.warning(
                        "[AUTH] token_version mismatch (header auth): user_id=%s",
                        user_id,
                    )
                    self._raise_token_lifecycle_error(error_code)
            return result

        try:
            validated_token = self.get_validated_token(raw_token)

            if not self._check_blacklist(validated_token):
                return None

            token_version = validated_token.get("token_version")
            logger.info(
                "[AUTH] token check: version=%s user_id=%s has_cookie_token=True",
                token_version,
                validated_token.get("user_id"),
            )
            if token_version is not None:
                user_id = validated_token.get("user_id")
                is_valid = is_token_version_valid(user_id, token_version)
                logger.info(
                    "[AUTH] token_version valid check: user_id=%s "
                    "token_version=%s is_valid=%s",
                    user_id,
                    token_version,
                    is_valid,
                )
                if not is_valid:
                    error_code = (
                        TokenErrorCode.PASSWORD_CHANGED
                        if get_user_token_invalid_reason(user_id) == "password_changed"
                        else TokenErrorCode.OTHER_DEVICE_LOGIN
                    )
                    logger.warning(
                        "[AUTH] FORCED LOGOUT: user %s token_version=%s is invalid",
                        user_id,
                        token_version,
                    )
                    self._raise_token_lifecycle_error(error_code)
            else:
                logger.info(
                    "[AUTH] No token_version in access token, allowing (old token)",
                )

            user = self.get_user(validated_token)
            if not user.is_active:
                raise exceptions.AuthenticationFailed(
                    {
                        "error_code": TokenErrorCode.ACCOUNT_DISABLED,
                        "message": get_token_error_message(
                            TokenErrorCode.ACCOUNT_DISABLED,
                        ),
                    },
                    code="account_disabled",
                )
            return (user, validated_token)

        except (InvalidToken, TokenError) as exc:
            self._raise_mapped_token_error(exc)

    def _raise_mapped_token_error(self, exc: Exception) -> None:
        error_code, _http_status = self._map_token_error(str(exc))
        raise exceptions.AuthenticationFailed(
            {
                "error_code": error_code,
                "message": get_token_error_message(error_code),
            },
            code=error_code.lower(),
        ) from exc

    def _check_blacklist(self, validated_token: Token) -> bool:
        jti = validated_token.get("jti")
        if jti and is_token_blacklisted(jti):
            raise exceptions.AuthenticationFailed(
                {
                    "error_code": TokenErrorCode.TOKEN_BLACKLISTED,
                    "message": get_token_error_message(
                        TokenErrorCode.TOKEN_BLACKLISTED,
                    ),
                },
                code="token_blacklisted",
            )
        return True

    def _map_token_error(self, error_msg: str) -> tuple[str, int]:
        msg_lower = error_msg.lower()

        if "expired" in msg_lower or "token is expired" in msg_lower:
            return TokenErrorCode.TOKEN_EXPIRED, 401
        if "invalid" in msg_lower or "malformed" in msg_lower:
            return TokenErrorCode.INVALID_TOKEN, 401
        if "signature" in msg_lower:
            return TokenErrorCode.INVALID_TOKEN, 401

        return TokenErrorCode.INVALID_TOKEN, 401

    def get_user(self, validated_token: Token) -> AbstractBaseUser:
        try:
            user_id = validated_token.get("user_id")
            user = User.objects.get(pk=user_id)
            if not user.is_active:
                self._raise_token_lifecycle_error(TokenErrorCode.ACCOUNT_DISABLED)
            return user
        except User.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed(
                {
                    "error_code": TokenErrorCode.INVALID_TOKEN,
                    "message": get_token_error_message(TokenErrorCode.INVALID_TOKEN),
                },
                code="invalid_token",
            ) from exc


class OptionalJWTAuthenticationFromCookies(JWTAuthenticationFromCookies):
    """Treat invalid/expired JWT cookies as anonymous instead of returning 401."""

    def authenticate(
        self,
        request: Request,
    ) -> tuple[AbstractBaseUser, Token] | None:
        try:
            return super().authenticate(request)
        except (exceptions.AuthenticationFailed, exceptions.PermissionDenied):
            return None
