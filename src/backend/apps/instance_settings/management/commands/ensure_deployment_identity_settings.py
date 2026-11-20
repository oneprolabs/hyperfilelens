"""Check effective identity settings without replacing runtime overrides."""

from __future__ import annotations

import os
import re
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand
from django.db import DatabaseError, transaction

from apps.configuration.services import runtime_settings as runtime

GOOGLE_CLIENT_ID_PATTERN = re.compile(
    r"^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$"
)
TURNSTILE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
BOOLEAN_VALUES = frozenset({"true", "false", "1", "0", "yes", "no", "on", "off"})


def _source(key: str, env_name: str) -> str:
    """Use the same Runtime > Deployment > Default sources as the getters."""
    return runtime.get_source(key, env_name=env_name, settings_attr=env_name)


def _enterprise_source() -> str:
    """Retain the legacy sign-up fallback used by enterprise_registration_enabled."""
    key = runtime.KEY_IDENTITY_ENTERPRISE_REGISTRATION
    name = "HFL_ENTERPRISE_REGISTRATION_ENABLED"
    setting = getattr(settings, name, None)
    if (
        runtime.has_runtime_override(key)
        or os.getenv(name, "").strip()
        or isinstance(setting, bool)
        or (isinstance(setting, str) and setting.strip())
    ):
        return _source(key, name)
    return _source(runtime.KEY_IDENTITY_EMAIL_SIGNUP, "HFL_EMAIL_SIGNUP_ENABLED")


class Command(BaseCommand):
    """Validate effective configuration and synchronize only derived OAuth data."""

    help = (
        "Check effective identity and SMTP settings and synchronize Google "
        "SocialApp data. Runtime overrides are never imported or overwritten."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        warnings: list[str] = []
        # Environment values are fallbacks, not instructions to mutate the
        # configuration table. In particular, an explicit runtime false must
        # survive deployment just as an explicit runtime true does.
        flags = (
            (runtime.KEY_IDENTITY_EMAIL_SIGNUP, "HFL_EMAIL_SIGNUP_ENABLED"),
            (
                runtime.KEY_IDENTITY_ENTERPRISE_REGISTRATION,
                "HFL_ENTERPRISE_REGISTRATION_ENABLED",
            ),
            (
                runtime.KEY_IDENTITY_PERSONAL_REGISTRATION,
                "HFL_PERSONAL_REGISTRATION_ENABLED",
            ),
            (runtime.KEY_IDENTITY_GOOGLE_OAUTH, "HFL_GOOGLE_OAUTH_ENABLED"),
            (runtime.KEY_IDENTITY_TURNSTILE_ENABLED, "TURNSTILE_ENABLED"),
        )
        for key, name in flags:
            value = os.getenv(name, "").strip().lower()
            if (
                not runtime.has_runtime_override(key)
                and value
                and value not in BOOLEAN_VALUES
            ):
                warnings.append(
                    f"Invalid deployment boolean for {name}; check the effective configuration."
                )

        google_enabled = runtime.google_oauth_enabled()
        removed_google_duplicates = 0
        if runtime.enterprise_identity_enabled() and runtime.get_bool(
            runtime.KEY_IDENTITY_GOOGLE_OAUTH,
            env_name="HFL_GOOGLE_OAUTH_ENABLED",
            settings_attr="HFL_GOOGLE_OAUTH_ENABLED",
        ):
            client_id = runtime.google_client_id()
            secret = runtime.google_client_secret()
            if not GOOGLE_CLIENT_ID_PATTERN.fullmatch(client_id):
                warnings.append("Invalid or missing effective Google OAuth client ID.")
            elif not secret or re.search(r"[\x00\r\n]", secret):
                warnings.append(
                    "Invalid or missing effective Google OAuth client secret."
                )
            else:
                try:
                    # SocialApp/Site rows are derived from the effective
                    # configuration. These are the only writes in this command.
                    with transaction.atomic():
                        result = runtime.sync_google_social_app()
                        removed_google_duplicates = result.removed_duplicates
                except (DatabaseError, ImproperlyConfigured, ValueError) as exc:
                    warnings.append(
                        f"Google OAuth synchronization failed ({type(exc).__name__})."
                    )

        turnstile_enabled = runtime.turnstile_enabled()
        if turnstile_enabled and (
            not TURNSTILE_KEY_PATTERN.fullmatch(runtime.turnstile_site_key())
            or not TURNSTILE_KEY_PATTERN.fullmatch(runtime.turnstile_secret_key())
        ):
            warnings.append(
                "Invalid or missing effective Turnstile credentials; the configured "
                "verification policy was not changed."
            )

        email = runtime.email_connection_kwargs()
        if email["configuration_error"]:
            warnings.append(
                "SMTP configuration is unavailable or invalid; runtime overrides were not changed."
            )

        enterprise_enabled = runtime.enterprise_registration_enabled()
        personal_enabled = runtime.personal_registration_enabled()
        self.stdout.write(
            "Email sign-up: "
            f"{'enabled' if enterprise_enabled or personal_enabled else 'disabled'} "
            "(effective registration modes)"
        )
        self._write_status(
            "Enterprise registration", enterprise_enabled, _enterprise_source()
        )
        self._write_status(
            "Personal registration",
            personal_enabled,
            _source(
                runtime.KEY_IDENTITY_PERSONAL_REGISTRATION,
                "HFL_PERSONAL_REGISTRATION_ENABLED",
            ),
        )
        self._write_status(
            "Google OAuth",
            google_enabled,
            _source(runtime.KEY_IDENTITY_GOOGLE_OAUTH, "HFL_GOOGLE_OAUTH_ENABLED"),
        )
        self._write_status(
            "Turnstile",
            turnstile_enabled,
            _source(runtime.KEY_IDENTITY_TURNSTILE_ENABLED, "TURNSTILE_ENABLED"),
        )
        self.stdout.write(
            f"SMTP: {'unavailable' if email['configuration_error'] else 'configured'} "
            f"({email['source']})"
        )
        if removed_google_duplicates:
            self.stdout.write(
                f"Google OAuth duplicate applications removed: {removed_google_duplicates}"
            )
        for warning in warnings:
            self.stderr.write(self.style.WARNING(f"WARNING: {warning}"))
        self.stdout.write(
            "HFL_IDENTITY_STATUS=" + ("warning" if warnings else "applied")
        )

    def _write_status(self, label: str, enabled: bool, source: str) -> None:
        self.stdout.write(f"{label}: {'enabled' if enabled else 'disabled'} ({source})")
