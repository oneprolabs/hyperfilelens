"""Revoke logins without relying on a path-restricted refresh cookie."""

from datetime import datetime, timezone

import jwt
from rest_framework_simplejwt.exceptions import TokenBackendError
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.state import token_backend

from apps.iam.services.token_service import (
    blacklist_all_user_tokens,
    blacklist_token,
    get_token_family_version,
    is_token_version_valid,
)


def _logout_claims(raw: str, token_type: str) -> dict | None:
    """Verify a logout proof; expired tokens can revoke, never authenticate.

    The signature, issuer, audience and token type are still verified. The
    caller must also check the live family/version so an old login cannot
    invalidate a later login.
    """
    if not raw:
        return None
    try:
        claims = jwt.decode(
            raw,
            token_backend.get_verifying_key(raw),
            algorithms=[token_backend.algorithm],
            audience=token_backend.audience,
            issuer=token_backend.issuer,
            leeway=token_backend.get_leeway(),
            options={
                "verify_exp": False,
                "verify_aud": token_backend.audience is not None,
                "require": ["exp", api_settings.JTI_CLAIM],
            },
        )
    except (jwt.InvalidTokenError, TokenBackendError):
        return None
    if claims.get(api_settings.TOKEN_TYPE_CLAIM) != token_type:
        return None
    return claims


def revoke_logout_tokens(
    access_token: str, refresh_token: str, logout_token: str = "",
) -> int | None:
    """Revoke via the current access identity or a revocation-only fallback."""
    for raw, token_type in (
        (access_token, "access"),
        (refresh_token, "refresh"),
        (logout_token, "logout"),
    ):
        claims = _logout_claims(raw, token_type)
        if claims is None:
            continue
        user_id = claims.get(api_settings.USER_ID_CLAIM)
        family = claims.get("family_id")
        version = claims.get("token_version")
        if (
            not isinstance(user_id, int)
            or isinstance(user_id, bool)
            or not isinstance(family, str)
            or not family
            or not isinstance(version, int)
            or isinstance(version, bool)
            or not is_token_version_valid(user_id, version)
            or get_token_family_version(user_id, family) != version
        ):
            continue
        if token_type == "access":
            blacklist_token(
                claims[api_settings.JTI_CLAIM],
                datetime.fromtimestamp(claims["exp"], tz=timezone.utc),
            )
        # Preserve the existing product's all-device logout semantics.
        blacklist_all_user_tokens(user_id, "user_logout")
        # A usable access proof identifies the account being signed out. Do not
        # also revoke an unrelated account from an inconsistent refresh cookie.
        return user_id
    return None
