"""Email login with optional Turnstile and HttpOnly cookie authentication."""

import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import logout as django_logout
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from common.http.public_api import AnonymousPublicViewMixin

from apps.iam.auth.authentication import OptionalJWTAuthenticationFromCookies
from apps.iam.auth.serializers import UserDetailsSerializer
from apps.iam.profile_models import Profile
from apps.iam.services.turnstile_verification import (
    credentials_and_turnstile_present,
    invalid_turnstile_fields,
    missing_turnstile_fields,
    turnstile_configured,
    turnstile_required,
    verify_turnstile_for_action,
)
from apps.iam.services.token_service import (
    blacklist_all_user_tokens,
    generate_token_family_id,
    store_refresh_token_family,
)

User = get_user_model()

logger = logging.getLogger(__name__)

LOGIN_MAX_ATTEMPTS = 3
LOGIN_LOCKOUT_SECONDS = 180  # 3 minutes


def _get_lockout_cache_key(user_id: int) -> str:
    return f"login_lockout:{user_id}"


def _get_attempts_cache_key(user_id: int) -> str:
    return f"login_attempts:{user_id}"


def _is_user_locked(user_id: int) -> bool:
    return cache.get(_get_lockout_cache_key(user_id), False)


def _increment_failed_attempt(user_id: int) -> int:
    key = _get_attempts_cache_key(user_id)
    attempts = cache.get(key, 0) + 1
    cache.set(key, attempts, timeout=LOGIN_LOCKOUT_SECONDS)
    return attempts


def _lock_user(user_id: int) -> None:
    cache.set(_get_lockout_cache_key(user_id), True, timeout=LOGIN_LOCKOUT_SECONDS)
    cache.delete(_get_attempts_cache_key(user_id))


def _clear_failed_attempts(user_id: int) -> None:
    cache.delete(_get_attempts_cache_key(user_id))
    cache.delete(_get_lockout_cache_key(user_id))


def _get_cookie_settings() -> dict:
    return getattr(settings, "COOKIE_AUTH", {})


def _set_token_cookies(response: Response, access_token: str, refresh_token: str, family_id: str) -> Response:
    """Set access and refresh tokens as HttpOnly cookies."""
    cookie_settings = _get_cookie_settings()

    access_token_cookie_name = cookie_settings.get("ACCESS_TOKEN_COOKIE_NAME", "access_token")
    refresh_token_cookie_name = cookie_settings.get("REFRESH_TOKEN_COOKIE_NAME", "refresh_token")

    # Access token cookie
    response.set_cookie(
        access_token_cookie_name,
        access_token,
        max_age=settings.SIMPLE_JWT.get("ACCESS_TOKEN_LIFETIME", 3600),
        httponly=cookie_settings.get("ACCESS_TOKEN_COOKIE_HTTPONLY", True),
        secure=cookie_settings.get("ACCESS_TOKEN_COOKIE_SECURE", True),
        samesite=cookie_settings.get("ACCESS_TOKEN_COOKIE_SAMESITE", "Lax"),
        path=cookie_settings.get("ACCESS_TOKEN_COOKIE_PATH", "/"),
    )

    # Refresh token cookie
    response.set_cookie(
        refresh_token_cookie_name,
        refresh_token,
        max_age=settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME", 86400),
        httponly=cookie_settings.get("REFRESH_TOKEN_COOKIE_HTTPONLY", True),
        secure=cookie_settings.get("REFRESH_TOKEN_COOKIE_SECURE", True),
        samesite=cookie_settings.get("REFRESH_TOKEN_COOKIE_SAMESITE", "Lax"),
        path=cookie_settings.get("REFRESH_TOKEN_COOKIE_PATH", "/api/v1/auth/token/refresh"),
    )

    # Store family_id in a separate cookie (not HttpOnly, so JS can read if needed)
    response.set_cookie(
        "token_family",
        family_id,
        max_age=settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME", 86400),
        httponly=False,  # Readable by JS for debugging
        secure=cookie_settings.get("REFRESH_TOKEN_COOKIE_SECURE", True),
        samesite=cookie_settings.get("REFRESH_TOKEN_COOKIE_SAMESITE", "Lax"),
        path="/",
    )

    return response


def _complete_token_login(
    *,
    request,
    user,
    response: Response,
    organization=None,
) -> Response:
    """Issue the cookie session shared by tenant and platform staff logins."""
    from apps.iam.services.login_audit import record_user_login

    record_user_login(user, request, organization=organization)

    family_id = generate_token_family_id()
    refresh = RefreshToken.for_user(user)
    refresh["family_id"] = family_id

    blacklist_all_user_tokens(user.id, "new_login")
    token_version = store_refresh_token_family(
        user.id,
        family_id,
        refresh.payload.get("jti"),
    )
    refresh["token_version"] = token_version

    _set_token_cookies(
        response,
        str(refresh.access_token),
        str(refresh),
        family_id,
    )
    django_logout(request)
    return response


def _clear_token_cookies(response: Response) -> Response:
    """Clear all token cookies."""
    cookie_settings = _get_cookie_settings()
    access_token_cookie_name = cookie_settings.get("ACCESS_TOKEN_COOKIE_NAME", "access_token")
    refresh_token_cookie_name = cookie_settings.get("REFRESH_TOKEN_COOKIE_NAME", "refresh_token")

    response.delete_cookie(access_token_cookie_name, path="/")
    response.delete_cookie(refresh_token_cookie_name, path="/api/v1/auth/token/refresh")
    response.delete_cookie("token_family", path="/")
    return response


def _build_error_response(code: str, message: str, fields: dict = None, http_status: int = status.HTTP_400_BAD_REQUEST, request=None) -> Response:
    """Build a standardized error response."""
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


def _is_pending_registration_user(user: User) -> bool:
    try:
        profile = Profile.objects.get(user=user)
    except Profile.DoesNotExist:
        return not user.is_active
    return not user.is_active and not profile.registration_completed


class EmailLoginView(AnonymousPublicViewMixin, APIView):
    """
    Email + password login with optional Turnstile verification.
    POST /api/v1/auth/email-login

    Returns tokens as HttpOnly cookies instead of JSON body.
    """

    @extend_schema(
        tags=["auth"],
        summary="Email login",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "email": {"type": "string", "description": "User email"},
                    "password": {"type": "string", "description": "User password"},
                    "turnstile_token": {
                        "type": "string",
                        "description": "Required when Turnstile is enabled",
                    },
                },
                "required": ["email", "password"],
            }
        },
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        # Check if authentication detected a token version mismatch (force logout from another session)
        auth_error = getattr(request, 'auth_error', None)
        email = (request.data.get("email") or "").strip().lower()
        password = request.data.get("password")

        if auth_error:
            if not email and not password:
                error_code = auth_error.get("error_code", "OTHER_DEVICE_LOGIN")
                message = auth_error.get("message", _("Your account was logged in from another device"))
                raise AuthenticationFailed(
                    detail={"error_code": error_code, "message": message},
                    code=error_code.lower(),
                )
            request._force_auth_user = None
            request._force_auth_token = None
            request.auth_error = None

        if not credentials_and_turnstile_present(
            request.data,
            ["email", "password"],
            request,
        ):
            missing_fields = missing_turnstile_fields(request.data, request)
            return _build_error_response(
                "VALIDATION_ERROR",
                _("Missing required fields"),
                fields={
                    **missing_fields,
                    "email": [_("Required")] if not email else [],
                    "password": [_("Required")] if not password else [],
                },
            )

        if turnstile_required(request) and not turnstile_configured():
            return _build_error_response(
                "TURNSTILE_MISCONFIGURED",
                _("Human verification is temporarily unavailable"),
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        user_obj = User.objects.filter(email=email).first()
        if user_obj is None or _is_pending_registration_user(user_obj):
            return _build_error_response(
                "EMAIL_NOT_REGISTERED",
                _("This email is not registered"),
                fields={"email": [_("This email is not registered")]},
            )

        if _is_user_locked(user_obj.id):
            return _build_error_response(
                "ACCOUNT_LOCKED",
                _("Account is temporarily locked due to multiple failed login attempts"),
                fields={"email": [_("Account is temporarily locked due to multiple failed login attempts. Please try again in 3 minutes.")]},
            )

        if not verify_turnstile_for_action(request.data, request, action="login"):
            return _build_error_response(
                "TURNSTILE_INVALID",
                _("Invalid or expired human verification"),
                fields=invalid_turnstile_fields(),
            )

        if not user_obj.check_password(password):
            attempts = _increment_failed_attempt(user_obj.id)
            if attempts >= LOGIN_MAX_ATTEMPTS:
                _lock_user(user_obj.id)
                return _build_error_response(
                    "ACCOUNT_LOCKED",
                    _("Account is temporarily locked"),
                    fields={"email": [_("Account is temporarily locked due to multiple failed login attempts. Please try again in 3 minutes.")]},
                )
            return _build_error_response(
                "INVALID_PASSWORD",
                _("Incorrect password"),
                fields={"password": [_("Incorrect password")]},
            )

        # Check if user is active
        if not user_obj.is_active:
            return _build_error_response(
                "ACCOUNT_DISABLED",
                _("Account has been disabled"),
                fields={"email": [_("Account has been disabled. Please contact support.")]},
                http_status=status.HTTP_403_FORBIDDEN,
            )

        # Clear failed attempts on successful password check
        _clear_failed_attempts(user_obj.id)

        # Get user's available organizations
        roles = []
        available_orgs = []
        try:
            from apps.iam.models import Membership
            from apps.iam.services.membership_service import authoritative_role

            memberships = Membership.objects.filter(user=user_obj, is_active=True).select_related("organization")
            roles = [authoritative_role(m) for m in memberships]
            available_orgs = [
                {
                    "org_key": m.organization.key,
                    "org_name": m.organization.name,
                    "role": authoritative_role(m),
                }
                for m in memberships
                if m.organization.is_active
            ]
        except Exception as e:
            logger.warning(f"Failed to get user roles: {e}")

        # Store user_id in session for org selection
        request.session['pending_user_id'] = user_obj.id
        # Explicitly save session to ensure it's persisted before returning response
        request.session.save()

        response = Response(
            {
                "code": "0000",
                "data": {
                    "user": {
                        "id": user_obj.id,
                        "email": user_obj.email,
                        "username": user_obj.username,
                        "is_staff": user_obj.is_staff,
                    },
                    "roles": roles,
                    "available_orgs": available_orgs,
                    "message": _("Select organization to continue"),
                },
            },
            status=status.HTTP_200_OK,
        )
        if user_obj.is_staff and not available_orgs:
            response.data["data"]["message"] = _("Login successful")
            return _complete_token_login(
                request=request,
                user=user_obj,
                response=response,
            )

        # Tenant accounts complete login after selecting their organization.
        return response


class OrgSelectView(AnonymousPublicViewMixin, APIView):
    """
    Handle org selection after credentials validation.
    POST /api/v1/auth/org-select

    User must have submitted credentials first (pending_user_id in session).
    """

    @extend_schema(
        tags=["auth"],
        summary="Select organization after credentials",
        request={
            "application/json": {
                "type": "object",
                "properties": {
                    "org_key": {"type": "string", "description": "Selected organization key"},
                },
                "required": ["org_key"],
            }
        },
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        from apps.iam.models import Membership

        # Get pending user from session
        pending_user_id = request.session.get('pending_user_id')

        if not pending_user_id:
            return Response(
                {
                    "code": "1001",
                    "error": {"message": _("Session expired. Please login again.")},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Get user
        user_obj = User.objects.filter(id=pending_user_id).first()
        if not user_obj:
            return Response(
                {
                    "code": "1001",
                    "error": {"message": _("Session expired. Please login again.")},
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Get org_key from request
        org_key = request.data.get("org_key")
        if not org_key:
            return Response(
                {
                    "code": "1001",
                    "error": {"message": _("Organization key is required")},
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Verify user is member of this org
        membership = Membership.objects.filter(
            user=user_obj,
            organization__key=org_key,
            is_active=True
        ).select_related("organization").first()

        if not membership:
            return Response(
                {
                    "code": "1001",
                    "error": {"message": _("You are not a member of this organization")},
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        from apps.iam.services.membership_service import authoritative_role

        # Generate tokens and complete login
        # Clear pending_user_id from session
        request.session.pop('pending_user_id', None)

        # Build response with cookies
        response = Response(
            {
                "code": "0000",
                "data": {
                    "user": {
                        "id": user_obj.id,
                        "email": user_obj.email,
                        "username": user_obj.username,
                        "is_staff": user_obj.is_staff,
                    },
                    "selected_org": {
                        "org_key": org_key,
                        "org_name": membership.organization.name,
                        "role": authoritative_role(membership),
                    },
                    "message": _("Login successful"),
                },
            },
            status=status.HTTP_200_OK,
        )

        return _complete_token_login(
            request=request,
            user=user_obj,
            response=response,
            organization=membership.organization,
        )


class TokenRefreshView(APIView):
    """
    Refresh access token using refresh token from cookie.
    Implements Refresh Token Rotation.

    POST /api/v1/auth/token/refresh
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["auth"],
        summary="Inspect the cookie session without producing an authentication error",
        responses={200: OpenApiTypes.OBJECT},
    )
    def get(self, request):
        """Return session availability with HTTP 200 for signed-out clients."""
        cookie_settings = getattr(settings, "COOKIE_AUTH", {})
        refresh_cookie_name = cookie_settings.get(
            "REFRESH_TOKEN_COOKIE_NAME",
            "refresh_token",
        )
        result = OptionalJWTAuthenticationFromCookies().authenticate(request)
        if result is None:
            response = Response(
                {
                    "authenticated": False,
                    "refresh_available": bool(
                        request.COOKIES.get(refresh_cookie_name),
                    ),
                    "user": None,
                },
                status=status.HTTP_200_OK,
            )
            response["Cache-Control"] = "no-store"
            return response

        user, _validated_token = result
        serializer = UserDetailsSerializer(user, context={"request": request})
        response = Response(
            {
                "authenticated": True,
                "refresh_available": bool(
                    request.COOKIES.get(refresh_cookie_name),
                ),
                "user": serializer.data,
            },
            status=status.HTTP_200_OK,
        )
        response["Cache-Control"] = "no-store"
        return response

    @extend_schema(
        tags=["auth"],
        summary="Refresh access token",
        responses={
            200: OpenApiTypes.OBJECT,
            401: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="The refresh token is absent, expired, invalid, or reused.",
            ),
            409: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description=(
                    "Another request is refreshing the same login session. "
                    "The client should wait and inspect the session again."
                ),
            ),
        },
    )
    def post(self, request):
        from django.conf import settings as django_settings
        from datetime import timedelta
        from rest_framework_simplejwt.exceptions import TokenError
        from apps.iam.services.token_service import (
            TokenError as TokenErrorCode,
            check_refresh_token_rotation,
            complete_refresh_token_rotation_claim,
            extend_refresh_token_family,
            get_token_error_message,
            release_refresh_token_rotation_claim,
        )

        # Check if authentication detected a token version mismatch
        auth_error = getattr(request, 'auth_error', None)
        if auth_error:
            error_code = auth_error.get("error_code", "OTHER_DEVICE_LOGIN")
            message = auth_error.get("message", _("Your account was logged in from another device"))
            raise AuthenticationFailed(
                detail={"error_code": error_code, "message": message},
                code=error_code.lower(),
            )

        cookie_settings = getattr(django_settings, "COOKIE_AUTH", {})
        refresh_cookie_name = cookie_settings.get("REFRESH_TOKEN_COOKIE_NAME", "refresh_token")
        refresh_token_str = request.COOKIES.get(refresh_cookie_name)

        if not refresh_token_str:
            return _build_error_response(
                "REFRESH_EXPIRED",
                _("Refresh token not found, please login again"),
                http_status=status.HTTP_401_UNAUTHORIZED,
            )

        try:
            refresh = RefreshToken(refresh_token_str)
            user_id = refresh.get("user_id")
            family_id = refresh.get("family_id")
            token_jti = refresh.get("jti")
            token_version = refresh.get("token_version", 0)

            # Check rotation
            is_valid, error_code, claim_id = check_refresh_token_rotation(
                user_id,
                family_id,
                token_jti,
            )
            if not is_valid:
                if error_code == TokenErrorCode.REFRESH_CONCURRENT:
                    response = _build_error_response(
                        error_code,
                        get_token_error_message(error_code),
                        http_status=status.HTTP_409_CONFLICT,
                    )
                    response["Retry-After"] = "1"
                    return response

                # Clear cookies on security events
                response = Response(
                    {
                        "code": "1001",
                        "error": {
                            "error_code": error_code,
                            "message": get_token_error_message(error_code),
                        },
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )
                _clear_token_cookies(response)
                return response

            try:
                # Get user
                try:
                    user = User.objects.get(pk=user_id)
                except User.DoesNotExist:
                    complete_refresh_token_rotation_claim(
                        user_id,
                        family_id,
                        token_jti,
                        claim_id,
                    )
                    response = _build_error_response(
                        "INVALID_TOKEN",
                        _("User not found"),
                        http_status=status.HTTP_401_UNAUTHORIZED,
                    )
                    _clear_token_cookies(response)
                    return response

                if not user.is_active:
                    complete_refresh_token_rotation_claim(
                        user_id,
                        family_id,
                        token_jti,
                        claim_id,
                    )
                    response = _build_error_response(
                        "ACCOUNT_DISABLED",
                        _("Account has been disabled"),
                        http_status=status.HTTP_401_UNAUTHORIZED,
                    )
                    _clear_token_cookies(response)
                    return response

                # Rotate refresh token and generate a matching access token.
                rotated_refresh = RefreshToken.for_user(user)
                rotated_refresh["family_id"] = family_id
                rotated_refresh["token_version"] = token_version
                access = rotated_refresh.access_token
                extend_refresh_token_family(user_id, family_id, token_version)
            except Exception:
                release_refresh_token_rotation_claim(
                    user_id,
                    family_id,
                    token_jti,
                    claim_id,
                )
                raise

            if not complete_refresh_token_rotation_claim(
                user_id,
                family_id,
                token_jti,
                claim_id,
            ):
                response = Response(
                    {
                        "code": "1001",
                        "error": {
                            "error_code": TokenErrorCode.TOKEN_REUSED,
                            "message": get_token_error_message(TokenErrorCode.TOKEN_REUSED),
                        },
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )
                _clear_token_cookies(response)
                return response

            # Build response
            response = Response(
                {
                    "code": "0000",
                    "data": {
                        "message": _("Token refreshed successfully"),
                    },
                },
                status=status.HTTP_200_OK,
            )

            # Update access token cookie
            access_cookie_name = cookie_settings.get("ACCESS_TOKEN_COOKIE_NAME", "access_token")
            response.set_cookie(
                access_cookie_name,
                str(access),
                max_age=int(django_settings.SIMPLE_JWT.get("ACCESS_TOKEN_LIFETIME", timedelta(hours=1)).total_seconds()),
                httponly=cookie_settings.get("ACCESS_TOKEN_COOKIE_HTTPONLY", True),
                secure=cookie_settings.get("ACCESS_TOKEN_COOKIE_SECURE", True),
                samesite=cookie_settings.get("ACCESS_TOKEN_COOKIE_SAMESITE", "Lax"),
                path="/",
            )
            refresh_cookie_name = cookie_settings.get("REFRESH_TOKEN_COOKIE_NAME", "refresh_token")
            response.set_cookie(
                refresh_cookie_name,
                str(rotated_refresh),
                max_age=int(django_settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME", timedelta(hours=24)).total_seconds()),
                httponly=cookie_settings.get("REFRESH_TOKEN_COOKIE_HTTPONLY", True),
                secure=cookie_settings.get("REFRESH_TOKEN_COOKIE_SECURE", True),
                samesite=cookie_settings.get("REFRESH_TOKEN_COOKIE_SAMESITE", "Lax"),
                path=cookie_settings.get("REFRESH_TOKEN_COOKIE_PATH", "/api/v1/auth/token/refresh"),
            )
            response.set_cookie(
                "token_family",
                family_id,
                max_age=int(django_settings.SIMPLE_JWT.get("REFRESH_TOKEN_LIFETIME", timedelta(hours=24)).total_seconds()),
                httponly=False,
                secure=cookie_settings.get("REFRESH_TOKEN_COOKIE_SECURE", True),
                samesite=cookie_settings.get("REFRESH_TOKEN_COOKIE_SAMESITE", "Lax"),
                path="/",
            )

            return response

        except TokenError as e:
            error_msg = str(e).lower()
            if "expired" in error_msg:
                error_code = TokenErrorCode.REFRESH_EXPIRED
            else:
                error_code = TokenErrorCode.INVALID_TOKEN

            response = _build_error_response(
                error_code,
                get_token_error_message(error_code),
                http_status=status.HTTP_401_UNAUTHORIZED,
            )
            _clear_token_cookies(response)
            return response


class LogoutView(AnonymousPublicViewMixin, APIView):
    """
    Logout user by blacklisting current tokens.
    POST /api/v1/auth/logout
    """

    @extend_schema(
        tags=["auth"],
        summary="Logout and invalidate tokens",
        responses={200: OpenApiTypes.OBJECT},
    )
    def post(self, request):
        from apps.iam.services.token_service import blacklist_all_user_tokens

        cookie_settings = getattr(settings, "COOKIE_AUTH", {})
        access_cookie_name = cookie_settings.get("ACCESS_TOKEN_COOKIE_NAME", "access_token")
        refresh_cookie_name = cookie_settings.get("REFRESH_TOKEN_COOKIE_NAME", "refresh_token")

        access_token_str = request.COOKIES.get(access_cookie_name)
        refresh_token_str = request.COOKIES.get(refresh_cookie_name)

        # Blacklist access token if present
        if access_token_str:
            try:
                from rest_framework_simplejwt.tokens import AccessToken
                access = AccessToken(access_token_str)
                jti = access.get("jti")
                exp = access.get("exp")
                if jti:
                    from apps.iam.services.token_service import blacklist_token
                    from datetime import datetime
                    from django.utils import timezone as tz
                    expires_at = datetime.fromtimestamp(exp, tz=tz.utc)
                    blacklist_token(jti, expires_at)
            except Exception:
                pass

        # Blacklist all user refresh tokens (logout from all devices option)
        # For single device logout, we'd need to track the specific refresh token
        # For now, we blacklist all if we can identify the user
        if refresh_token_str:
            try:
                from rest_framework_simplejwt.tokens import RefreshToken
                refresh = RefreshToken(refresh_token_str)
                user_id = refresh.get("user_id")
                if user_id:
                    blacklist_all_user_tokens(user_id, "user_logout")
            except Exception:
                pass

        # Clear session
        request.session.flush()

        response = Response(
            {
                "code": "0000",
                "data": {"message": _("Logged out successfully")},
            },
            status=status.HTTP_200_OK,
        )
        response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
        _clear_token_cookies(response)
        return response
