"""Issue JWT auth cookies after a successful login (email or OAuth)."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.http import HttpResponseBase
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken, Token

from apps.iam.models import Membership
from apps.iam.services.login_organizations import login_memberships
from apps.iam.services.login_audit import record_user_login
from apps.iam.services.token_service import (
    blacklist_all_user_tokens,
    generate_token_family_id,
    store_refresh_token_family,
)

LOGOUT_COOKIE_NAME = "hfl_logout"
LOGOUT_COOKIE_PATH = "/api/v1/auth/logout"


class LogoutToken(Token):
    """Signed revocation proof, never an access or refresh credential."""

    token_type = "logout"
    lifetime = timedelta(days=1)


def _cookie_max_age(key: str, default_seconds: int) -> int:
    lifetime = settings.SIMPLE_JWT.get(key, default_seconds)
    if hasattr(lifetime, "total_seconds"):
        return int(lifetime.total_seconds())
    return int(lifetime)


def _get_cookie_settings() -> dict:
    return getattr(settings, "COOKIE_AUTH", {})


def set_logout_cookie(response: HttpResponseBase, claims) -> None:
    """Keep logout verifiable after the browser removes an expired access cookie.

    Also used when starting impersonation from a pre-existing login. A separate
    name/path leaves legacy refresh cookies untouched and avoids duplicate-name
    cookie migration ambiguity.
    """
    proof = LogoutToken()
    for key in (api_settings.USER_ID_CLAIM, "family_id", "token_version"):
        proof[key] = claims[key]
    lifetime = _cookie_max_age("REFRESH_TOKEN_LIFETIME", 86400)
    proof.set_exp(lifetime=timedelta(seconds=lifetime))
    cookie_settings = _get_cookie_settings()
    response.set_cookie(
        LOGOUT_COOKIE_NAME,
        str(proof),
        max_age=lifetime,
        httponly=True,
        secure=cookie_settings.get("REFRESH_TOKEN_COOKIE_SECURE", True),
        samesite="Strict",
        path=LOGOUT_COOKIE_PATH,
    )


def clear_auth_cookies(response: HttpResponseBase) -> HttpResponseBase:
    cookie_settings = _get_cookie_settings()
    response.delete_cookie(
        cookie_settings.get("ACCESS_TOKEN_COOKIE_NAME", "access_token"),
        path=cookie_settings.get("ACCESS_TOKEN_COOKIE_PATH", "/"),
    )
    response.delete_cookie(
        cookie_settings.get("REFRESH_TOKEN_COOKIE_NAME", "refresh_token"),
        path=cookie_settings.get("REFRESH_TOKEN_COOKIE_PATH", "/api/v1/auth/token/refresh"),
    )
    response.delete_cookie("token_family", path="/")
    response.delete_cookie(LOGOUT_COOKIE_NAME, path=LOGOUT_COOKIE_PATH, samesite="Strict")
    return response


def set_auth_cookies(
    response: HttpResponseBase,
    access_token: str,
    refresh_token: str,
    family_id: str,
) -> HttpResponseBase:
    cookie_settings = _get_cookie_settings()
    access_name = cookie_settings.get("ACCESS_TOKEN_COOKIE_NAME", "access_token")
    refresh_name = cookie_settings.get("REFRESH_TOKEN_COOKIE_NAME", "refresh_token")

    response.set_cookie(
        access_name,
        access_token,
        max_age=_cookie_max_age("ACCESS_TOKEN_LIFETIME", 3600),
        httponly=cookie_settings.get("ACCESS_TOKEN_COOKIE_HTTPONLY", True),
        secure=cookie_settings.get("ACCESS_TOKEN_COOKIE_SECURE", True),
        samesite=cookie_settings.get("ACCESS_TOKEN_COOKIE_SAMESITE", "Lax"),
        path=cookie_settings.get("ACCESS_TOKEN_COOKIE_PATH", "/"),
    )
    response.set_cookie(
        refresh_name,
        refresh_token,
        max_age=_cookie_max_age("REFRESH_TOKEN_LIFETIME", 86400),
        httponly=cookie_settings.get("REFRESH_TOKEN_COOKIE_HTTPONLY", True),
        secure=cookie_settings.get("REFRESH_TOKEN_COOKIE_SECURE", True),
        samesite=cookie_settings.get("REFRESH_TOKEN_COOKIE_SAMESITE", "Lax"),
        path=cookie_settings.get("REFRESH_TOKEN_COOKIE_PATH", "/api/v1/auth/token/refresh"),
    )
    response.set_cookie(
        "token_family",
        family_id,
        max_age=_cookie_max_age("REFRESH_TOKEN_LIFETIME", 86400),
        httponly=False,
        secure=cookie_settings.get("REFRESH_TOKEN_COOKIE_SECURE", True),
        samesite=cookie_settings.get("REFRESH_TOKEN_COOKIE_SAMESITE", "Lax"),
        path="/",
    )
    set_logout_cookie(response, AccessToken(access_token))
    return response


def issue_auth_tokens_for_user(user, request) -> tuple[str, str, str, Membership | None]:
    membership = login_memberships(user).first()
    if membership is not None:
        record_user_login(user, request, organization=membership.organization)

    family_id = generate_token_family_id()
    refresh = RefreshToken.for_user(user)
    refresh["family_id"] = family_id
    refresh["auth_time"] = refresh["iat"]
    blacklist_all_user_tokens(user.id, "new_login")
    token_version = store_refresh_token_family(user.id, family_id, refresh.payload.get("jti"))
    refresh["token_version"] = token_version
    return str(refresh.access_token), str(refresh), family_id, membership
