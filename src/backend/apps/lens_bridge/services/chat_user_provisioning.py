"""Provision and token management for SourceLens chat-only users."""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
from typing import Any

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser

from apps.lens_bridge.models import LensSlUserLink
from apps.lens_bridge.services import sl_client

logger = logging.getLogger(__name__)

_USER_TOKEN_LOCK = threading.Lock()
_USER_TOKENS: dict[int, tuple[str, float]] = {}


def sl_username_for_hfl_user(user: AbstractBaseUser) -> str:
    return f"hfl-u-{user.pk}"


def sl_email_for_hfl_user(user: AbstractBaseUser) -> str:
    """Return the private email identifier used only by bundled SourceLens."""
    return f"hfl-u-{user.pk}@users.hyperfilelens.invalid"


def _sl_password_for_hfl_user(user: AbstractBaseUser) -> str:
    """Derive a stable server-only password for an HFL-managed SL account."""
    digest = hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        f"sourcelens-chat-user:{user.pk}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"Hfl!{digest}"


def _sl_answer_language(language: str | None) -> str:
    """Map an HFL language code to the SourceLens answer language value.

    SourceLens resolves the answer language from ``profile.language``. Keep
    HFL language-pack variants aligned with SourceLens' canonical values while
    preserving English as the safe fallback for unsupported languages.
    """
    code = (language or "").strip().lower()
    if code.startswith("zh"):
        return "zh-CN"
    if code == "es" or code.startswith("es-"):
        return "es"
    return "en-US"


def _sl_answer_language_for_hfl_user(user: AbstractBaseUser) -> str:
    """Derive the SourceLens answer language from the HFL user's profile."""
    profile = getattr(user, "profile", None)
    return _sl_answer_language(getattr(profile, "language", None))


def _find_remote_user(username: str) -> dict[str, Any] | None:
    page = 1
    page_size = 100
    while True:
        payload = sl_client.request_json(
            "GET",
            "/api/v1/management/users/",
            params={"page": page, "page_size": page_size},
        )
        if not isinstance(payload, dict):
            raise sl_client.LensBridgeError("Unexpected SourceLens user list response.")
        rows = payload.get("results") or []
        if not isinstance(rows, list):
            raise sl_client.LensBridgeError("Unexpected SourceLens user list results.")
        for row in rows:
            if isinstance(row, dict) and row.get("username") == username:
                return row
        raw_total = payload.get("count", 0)
        if isinstance(raw_total, bool) or not isinstance(raw_total, (int, str)):
            raise sl_client.LensBridgeError("Unexpected SourceLens user list count.")
        try:
            total = int(raw_total)
        except (TypeError, ValueError):
            raise sl_client.LensBridgeError(
                "Unexpected SourceLens user list count."
            ) from None
        if total < 0:
            raise sl_client.LensBridgeError("Unexpected SourceLens user list count.")
        if not rows or page * page_size >= total:
            return None
        page += 1


def ensure_sl_chat_user(
    user: AbstractBaseUser,
    *,
    gateway_operator: bool = False,
) -> LensSlUserLink:
    """Idempotently provision an SL chat user for the given HFL user."""

    link = LensSlUserLink.objects.filter(hfl_user=user).first()
    desired_email = sl_email_for_hfl_user(user)
    if (
        link is not None
        and link.provision_status == LensSlUserLink.ProvisionStatus.READY
        and link.gateway_operator == gateway_operator
        and link.sl_email == desired_email
    ):
        return link

    if link is None:
        link = LensSlUserLink.objects.create(
            hfl_user=user,
            sl_user_id=0,
            sl_username=sl_username_for_hfl_user(user),
            sl_email=desired_email,
            gateway_operator=gateway_operator,
            provision_status=LensSlUserLink.ProvisionStatus.PENDING,
        )

    try:
        _provision_remote(user, link=link, gateway_operator=gateway_operator)
    except sl_client.LensBridgeError as exc:
        link.provision_status = LensSlUserLink.ProvisionStatus.ERROR
        link.last_error = str(exc.detail if hasattr(exc, "detail") else exc)[:2000]
        link.save(update_fields=["provision_status", "last_error", "updated_at"])
        raise

    return link


def _provision_remote(
    user: AbstractBaseUser,
    *,
    link: LensSlUserLink,
    gateway_operator: bool,
) -> None:
    username = sl_username_for_hfl_user(user)
    email = sl_email_for_hfl_user(user)
    payload = _find_remote_user(username)
    if payload is None:
        try:
            payload = sl_client.request_json(
                "POST",
                "/api/v1/management/users/",
                json_body={
                    "username": username,
                    "email": email,
                    "password": _sl_password_for_hfl_user(user),
                    "is_staff": False,
                    "role_ids": [],
                    "preferred_platform": "workspace",
                    "language": _sl_answer_language_for_hfl_user(user),
                },
            )
        except sl_client.LensBridgeError:
            payload = _find_remote_user(username)
            if payload is None:
                raise
    if not isinstance(payload, dict):
        raise sl_client.LensBridgeError(
            "Unexpected provision response from SourceLens."
        )

    raw_user_id = payload.get("id") or 0
    if isinstance(raw_user_id, bool):
        raise sl_client.LensBridgeError("SourceLens provision returned no user id.")
    try:
        sl_user_id = int(raw_user_id)
    except (TypeError, ValueError):
        raise sl_client.LensBridgeError(
            "SourceLens provision returned no user id."
        ) from None
    if sl_user_id <= 0:
        raise sl_client.LensBridgeError("SourceLens provision returned no user id.")
    if str(payload.get("email") or "").strip().lower() != email.lower():
        updated = sl_client.request_json(
            "PATCH",
            f"/api/v1/management/users/{sl_user_id}/",
            json_body={"email": email},
        )
        if not isinstance(updated, dict):
            raise sl_client.LensBridgeError(
                "SourceLens email migration returned an invalid response."
            )
        payload = updated
    confirmed_email = str(payload.get("email") or "").strip().lower()
    if confirmed_email != email.lower():
        raise sl_client.LensBridgeError(
            "SourceLens did not confirm the migrated chat user email."
        )

    desired_language = _sl_answer_language_for_hfl_user(user)
    if payload.get("language") != desired_language:
        updated = sl_client.request_json(
            "PATCH",
            f"/api/v1/management/users/{sl_user_id}/",
            json_body={"language": desired_language},
        )
        if not isinstance(updated, dict):
            raise sl_client.LensBridgeError(
                "SourceLens language sync returned an invalid response."
            )
        payload = updated

    link.sl_user_id = sl_user_id
    link.sl_username = str(payload.get("username") or link.sl_username)
    link.sl_email = email
    link.gateway_operator = gateway_operator
    link.provision_status = LensSlUserLink.ProvisionStatus.READY
    link.last_error = ""
    link.save(
        update_fields=[
            "sl_user_id",
            "sl_username",
            "sl_email",
            "gateway_operator",
            "provision_status",
            "last_error",
            "updated_at",
        ]
    )
    with _USER_TOKEN_LOCK:
        _USER_TOKENS.pop(user.pk, None)


def sync_sl_user_language(user: AbstractBaseUser, language: str | None) -> bool:
    """Best-effort sync of a user's answer language to the SL chat profile.

    SourceLens resolves each run's answer language from the profile, so keeping
    it in sync makes new runs answer in the language the user chose in the UI.
    Returns ``False`` (without raising) when there is no ready SL link or the
    remote update fails; ``True`` when the language was pushed successfully.
    """
    link = LensSlUserLink.objects.filter(
        hfl_user=user,
        provision_status=LensSlUserLink.ProvisionStatus.READY,
    ).first()
    if link is None:
        return False
    sl_language = _sl_answer_language(language)
    try:
        updated = sl_client.request_json(
            "PATCH",
            f"/api/v1/management/users/{link.sl_user_id}/",
            json_body={"language": sl_language},
        )
    except sl_client.LensBridgeError:
        logger.warning(
            "Failed to sync answer language for HFL user %s to SourceLens.",
            user.pk,
            exc_info=True,
        )
        return False
    if not isinstance(updated, dict):
        logger.warning(
            "Unexpected response while syncing answer language for HFL user %s.",
            user.pk,
        )
        return False
    with _USER_TOKEN_LOCK:
        _USER_TOKENS.pop(user.pk, None)
    return True


def mint_sl_access_token(user: AbstractBaseUser) -> str:
    """Return a cached or freshly minted JWT for the user's SL chat account."""

    link = ensure_sl_chat_user(user)
    now = time.time()
    with _USER_TOKEN_LOCK:
        cached = _USER_TOKENS.get(user.pk)
        if cached and cached[1] > now:
            return cached[0]

    token = sl_client.login_user(
        email=link.sl_email,
        password=_sl_password_for_hfl_user(user),
        legacy_username=link.sl_username,
    )
    with _USER_TOKEN_LOCK:
        _USER_TOKENS[user.pk] = (token, now + 25 * 60)
    return token


def invalidate_user_token(user_id: int) -> None:
    with _USER_TOKEN_LOCK:
        _USER_TOKENS.pop(user_id, None)


def enqueue_sl_chat_user_provision(
    *, user_id: int, gateway_operator: bool = False
) -> None:
    """Best-effort async provisioning after registration."""

    from apps.lens_bridge.services.sync_queue import queue_sl_chat_user_provision

    queue_sl_chat_user_provision(user_id=user_id, gateway_operator=gateway_operator)
