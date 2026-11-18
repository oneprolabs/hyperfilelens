"""Apply deployment-managed optional identity and email settings."""

from __future__ import annotations

import os
import re
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand
from django.db import DatabaseError, transaction

from apps.configuration.services.runtime_settings import (
    KEY_EMAIL_BACKEND,
    KEY_EMAIL_FROM,
    KEY_EMAIL_HOST,
    KEY_EMAIL_HOST_USER,
    KEY_EMAIL_PORT,
    KEY_EMAIL_USE_SSL,
    KEY_EMAIL_USE_TLS,
    KEY_IDENTITY_EMAIL_SIGNUP,
    KEY_IDENTITY_ENTERPRISE_REGISTRATION,
    KEY_IDENTITY_PERSONAL_REGISTRATION,
    KEY_IDENTITY_GOOGLE_CLIENT_ID,
    KEY_IDENTITY_GOOGLE_OAUTH,
    KEY_IDENTITY_TURNSTILE_SITE,
    SECRET_KEY_EMAIL_PASSWORD,
    SECRET_KEY_GOOGLE,
    SECRET_KEY_TURNSTILE,
    SMTP_EMAIL_BACKEND,
    set_bool,
    set_value,
    sync_google_social_app,
)

GOOGLE_CLIENT_ID_PATTERN = re.compile(
    r"^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$"
)
TURNSTILE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def _parse_bool(name: str) -> bool | None:
    value = os.getenv(name, "").strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


class Command(BaseCommand):
    """Synchronize deployment identity settings without external connectivity tests."""

    help = (
        "Synchronize deployment-managed sign-up, Google OAuth, Turnstile, "
        "and SMTP settings."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        legacy_signup_enabled = _parse_bool("HFL_EMAIL_SIGNUP_ENABLED")
        enterprise_registration_enabled = _parse_bool(
            "HFL_ENTERPRISE_REGISTRATION_ENABLED"
        )
        personal_registration_enabled = _parse_bool(
            "HFL_PERSONAL_REGISTRATION_ENABLED"
        )
        google_enabled = _parse_bool("HFL_GOOGLE_OAUTH_ENABLED")
        google_client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
        google_client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "")
        turnstile_enabled = _parse_bool("TURNSTILE_ENABLED")
        warnings: list[str] = []
        removed_google_duplicates = 0

        if (
            enterprise_registration_enabled is None
            and os.getenv("HFL_ENTERPRISE_REGISTRATION_ENABLED", "").strip()
        ):
            warnings.append(
                "Invalid Enterprise registration flag; preserved the installed "
                "runtime setting."
            )
        if (
            personal_registration_enabled is None
            and os.getenv("HFL_PERSONAL_REGISTRATION_ENABLED", "").strip()
        ):
            warnings.append(
                "Invalid Personal registration flag; preserved the installed "
                "runtime setting."
            )

        with transaction.atomic():
            if legacy_signup_enabled is None:
                warnings.append(
                    "Invalid email sign-up flag; preserved the installed runtime setting."
                )
            else:
                set_bool(KEY_IDENTITY_EMAIL_SIGNUP, legacy_signup_enabled)

            if enterprise_registration_enabled is None:
                # The legacy deployment flag is the compatibility source for
                # Enterprise registration until a new flag is supplied. Do
                # not materialize a second row for legacy-only deployments.
                pass
            else:
                set_bool(
                    KEY_IDENTITY_ENTERPRISE_REGISTRATION,
                    enterprise_registration_enabled,
                )

            if personal_registration_enabled is not None:
                set_bool(
                    KEY_IDENTITY_PERSONAL_REGISTRATION,
                    personal_registration_enabled,
                )

            if google_enabled is None:
                warnings.append(
                    "Invalid Google OAuth flag; preserved the installed Google settings."
                )
            elif not google_enabled:
                set_bool(KEY_IDENTITY_GOOGLE_OAUTH, False)
            elif not GOOGLE_CLIENT_ID_PATTERN.fullmatch(google_client_id):
                warnings.append(
                    "Invalid Google OAuth client ID; preserved the installed Google settings."
                )
            elif not google_client_secret or re.search(
                r"[\x00\r\n]", google_client_secret
            ):
                warnings.append(
                    "Invalid Google OAuth client secret; preserved the installed Google settings."
                )
            else:
                try:
                    with transaction.atomic():
                        set_value(
                            key=KEY_IDENTITY_GOOGLE_CLIENT_ID,
                            value=google_client_id,
                        )
                        set_value(key=SECRET_KEY_GOOGLE, secret=google_client_secret)
                        set_bool(KEY_IDENTITY_GOOGLE_OAUTH, True)
                        sync_result = sync_google_social_app()
                        removed_google_duplicates = sync_result.removed_duplicates
                except (DatabaseError, ImproperlyConfigured, ValueError) as exc:
                    warnings.append(
                        "Google OAuth settings could not be synchronized; preserved "
                        "the installed Google settings."
                    )
                    self.stderr.write(
                        self.style.WARNING(
                            "Google OAuth synchronization failed "
                            f"({type(exc).__name__})."
                        )
                    )

            self._sync_turnstile(turnstile_enabled, warnings)
            self._sync_email(warnings)

        for warning in warnings:
            self.stderr.write(self.style.WARNING(warning))
        self.stdout.write(
            "Email sign-up: "
            + ("enabled" if legacy_signup_enabled else "disabled")
            if legacy_signup_enabled is not None
            else "Email sign-up: preserved"
        )
        self.stdout.write(
            "Enterprise registration: "
            + (
                "enabled"
                if (
                    enterprise_registration_enabled
                    if enterprise_registration_enabled is not None
                    else legacy_signup_enabled
                )
                else "disabled"
            )
            if (
                enterprise_registration_enabled is not None
                or legacy_signup_enabled is not None
            )
            else "preserved"
        )
        self.stdout.write(
            "Personal registration: "
            + (
                "enabled" if personal_registration_enabled else "disabled"
            )
            if personal_registration_enabled is not None
            else "preserved"
        )
        self.stdout.write(
            "Google OAuth: "
            + ("enabled" if google_enabled else "disabled")
            if google_enabled is not None and not any(
                "Google OAuth" in warning for warning in warnings
            )
            else "Google OAuth: preserved"
        )
        if removed_google_duplicates:
            self.stdout.write(
                "Google OAuth duplicate applications removed: "
                f"{removed_google_duplicates}"
            )
        self.stdout.write(
            "HFL_IDENTITY_STATUS=" + ("warning" if warnings else "applied")
        )

    @staticmethod
    def _sync_turnstile(
        enabled: bool | None,
        warnings: list[str],
    ) -> None:
        site_key = os.getenv("TURNSTILE_SITE_KEY", "").strip()
        secret_key = os.getenv("TURNSTILE_SECRET_KEY", "")
        if enabled is None:
            warnings.append(
                "Invalid Turnstile flag; preserved the installed Turnstile settings."
            )
            return
        if not site_key and not secret_key and not enabled:
            return
        if not TURNSTILE_KEY_PATTERN.fullmatch(
            site_key
        ) or not TURNSTILE_KEY_PATTERN.fullmatch(secret_key):
            warnings.append(
                "Invalid Turnstile credentials; preserved the installed Turnstile settings."
            )
            return
        set_value(key=KEY_IDENTITY_TURNSTILE_SITE, value=site_key)
        set_value(key=SECRET_KEY_TURNSTILE, secret=secret_key)

    @staticmethod
    def _sync_email(
        warnings: list[str],
    ) -> None:
        backend = os.getenv("EMAIL_BACKEND", "").strip()
        if backend != SMTP_EMAIL_BACKEND:
            warnings.append(
                "Deployment-managed SMTP is unavailable; preserved the installed "
                "email settings."
            )
            return

        host = os.getenv("EMAIL_HOST", "").strip()
        port = os.getenv("EMAIL_PORT", "").strip()
        username = os.getenv("EMAIL_HOST_USER", "").strip()
        password = os.getenv("EMAIL_HOST_PASSWORD", "")
        from_email = os.getenv("DEFAULT_FROM_EMAIL", "").strip()
        use_tls = _parse_bool("EMAIL_USE_TLS")
        use_ssl = _parse_bool("EMAIL_USE_SSL")
        try:
            parsed_port = int(port)
        except ValueError:
            parsed_port = 0
        if (
            not host
            or re.search(r"\s", host)
            or parsed_port < 1
            or parsed_port > 65535
            or not username
            or not password
            or re.search(r"[\x00\r\n]", password)
            or not from_email
            or use_tls is None
            or use_ssl is None
            or (use_tls and use_ssl)
        ):
            warnings.append(
                "Invalid SMTP settings; preserved the installed email configuration."
            )
            return

        set_value(key=KEY_EMAIL_BACKEND, value=SMTP_EMAIL_BACKEND)
        set_value(key=KEY_EMAIL_HOST, value=host)
        set_value(key=KEY_EMAIL_PORT, value=str(parsed_port))
        set_bool(KEY_EMAIL_USE_TLS, use_tls)
        set_bool(KEY_EMAIL_USE_SSL, use_ssl)
        set_value(key=KEY_EMAIL_HOST_USER, value=username)
        set_value(key=SECRET_KEY_EMAIL_PASSWORD, secret=password)
        set_value(key=KEY_EMAIL_FROM, value=from_email)
