"""Read/write platform runtime settings with .env / Django settings fallback."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, transaction

from apps.configuration.models.platform_runtime_setting import PlatformRuntimeSetting
from apps.storage.crypto import decrypt_text, encrypt_text

logger = logging.getLogger(__name__)

KEY_EMAIL_BACKEND = "email.backend"
KEY_EMAIL_HOST = "email.host"
KEY_EMAIL_PORT = "email.port"
KEY_EMAIL_USE_TLS = "email.use_tls"
KEY_EMAIL_USE_SSL = "email.use_ssl"
KEY_EMAIL_HOST_USER = "email.host_user"
KEY_EMAIL_FROM = "email.from_email"

KEY_IDENTITY_EMAIL_SIGNUP = "identity.email_signup_enabled"
KEY_IDENTITY_EMAIL_CODE_LOGIN = "identity.email_code_login_enabled"
KEY_IDENTITY_PLATFORM_OPS = "identity.platform_ops_enabled"
KEY_IDENTITY_OPS_CIDRS = "identity.platform_ops_allowed_cidrs"
KEY_IDENTITY_TURNSTILE_SITE = "identity.turnstile_site_key"
KEY_IDENTITY_GOOGLE_CLIENT_ID = "identity.google_client_id"
KEY_IDENTITY_GOOGLE_OAUTH = "identity.google_oauth_enabled"

KEY_AI_OPENAI_BASE = "ai.openai_api_base"
KEY_AI_AZURE_BASE = "ai.azure_openai_api_base"
KEY_AI_LANGFUSE_BASE = "ai.langfuse_base_url"
KEY_AI_LANGFUSE_ENABLED = "ai.langfuse_enabled"

SECRET_KEYS = frozenset(
    {
        "email.host_password",
        "identity.turnstile_secret_key",
        "identity.google_client_secret",
        "ai.openai_api_key",
        "ai.azure_openai_api_key",
        "ai.gemini_api_key",
        "ai.langfuse_public_key",
        "ai.langfuse_secret_key",
    }
)

SECRET_KEY_EMAIL_PASSWORD = "email.host_password"
SECRET_KEY_TURNSTILE = "identity.turnstile_secret_key"
SECRET_KEY_GOOGLE = "identity.google_client_secret"
SECRET_KEY_OPENAI = "ai.openai_api_key"
SECRET_KEY_AZURE = "ai.azure_openai_api_key"
SECRET_KEY_GEMINI = "ai.gemini_api_key"
SECRET_KEY_LANGFUSE_PUBLIC = "ai.langfuse_public_key"
SECRET_KEY_LANGFUSE_SECRET = "ai.langfuse_secret_key"

SMTP_EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
CONSOLE_EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DUMMY_EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
IN_MEMORY_EMAIL_BACKENDS = frozenset(
    {
        "django.core.mail.backends.locmem.EmailBackend",
        "django.core.mail.backends.filebased.EmailBackend",
    }
)
EMAIL_RUNTIME_KEYS = (
    KEY_EMAIL_BACKEND,
    KEY_EMAIL_HOST,
    KEY_EMAIL_PORT,
    KEY_EMAIL_USE_TLS,
    KEY_EMAIL_USE_SSL,
    KEY_EMAIL_HOST_USER,
    SECRET_KEY_EMAIL_PASSWORD,
    KEY_EMAIL_FROM,
)

IDENTITY_RUNTIME_KEYS = (
    KEY_IDENTITY_EMAIL_SIGNUP,
    KEY_IDENTITY_EMAIL_CODE_LOGIN,
    KEY_IDENTITY_PLATFORM_OPS,
    KEY_IDENTITY_OPS_CIDRS,
    KEY_IDENTITY_TURNSTILE_SITE,
    KEY_IDENTITY_GOOGLE_CLIENT_ID,
    KEY_IDENTITY_GOOGLE_OAUTH,
    SECRET_KEY_TURNSTILE,
    SECRET_KEY_GOOGLE,
)


@dataclass(frozen=True)
class GoogleSocialAppSyncResult:
    """Describe one idempotent django-allauth Google application sync."""

    configured: bool
    app_id: int | None = None
    site_domain: str = ""
    removed_duplicates: int = 0


def invalidate_runtime_settings_cache() -> None:
    """Retained compatibility hook for callers that update runtime settings.

    Runtime settings are intentionally read from PostgreSQL on every access.
    Process-local caching made security and AI settings remain stale in Celery,
    Daphne, additional Gunicorn workers, and horizontally scaled API replicas.
    """


def _row(key: str) -> PlatformRuntimeSetting | None:
    """Read one current setting without process-local caching."""
    return PlatformRuntimeSetting.objects.filter(key=key).first()


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _settings_str(attr: str, default: str = "") -> str:
    return str(getattr(settings, attr, default) or "").strip()


def _parse_bool(raw: str, *, default: bool) -> bool:
    if raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _runtime_raw(key: str) -> tuple[str, bool]:
    """Return (value, is_runtime). Secret values only when needed internally."""
    row = _row(key)
    if row is None:
        return "", False
    if row.secret_ciphertext:
        return decrypt_text(row.secret_ciphertext), True
    return (row.value_text or "").strip(), True


def has_runtime_override(key: str) -> bool:
    """Whether a Runtime row exists (including explicit empty values)."""
    return _row(key) is not None


def get_source(
    key: str,
    *,
    env_name: str = "",
    settings_attr: str = "",
) -> str:
    """Return runtime | deployment | default for one PlatformRuntimeSetting key.

    Matches resolve semantics: empty env / empty settings string = unset
    (not Deployment). Boolean Django settings defaults alone do not count as
    Deployment (they are indistinguishable from code Default).
    """
    if has_runtime_override(key):
        return "runtime"
    if env_name and _env_str(env_name):
        return "deployment"
    if settings_attr:
        val = getattr(settings, settings_attr, None)
        if isinstance(val, str) and val.strip():
            return "deployment"
    return "default"


def get_str(key: str, *, env_name: str = "", settings_attr: str = "", default: str = "") -> str:
    """Resolve str: Runtime (incl. explicit empty) > Deployment > Default."""
    raw, from_runtime = _runtime_raw(key)
    if from_runtime:
        return raw
    if env_name:
        val = _env_str(env_name)
        if val:
            return val
    if settings_attr:
        val = _settings_str(settings_attr)
        if val:
            return val
    return default


def get_secret(key: str, *, env_name: str = "", settings_attr: str = "", default: str = "") -> str:
    """Resolve secret: Runtime (incl. explicit empty) > Deployment > Default."""
    raw, from_runtime = _runtime_raw(key)
    if from_runtime:
        return raw
    if env_name:
        val = _env_str(env_name)
        if val:
            return val
    if settings_attr:
        return _settings_str(settings_attr, default)
    return default


def get_bool(
    key: str,
    *,
    env_name: str = "",
    settings_attr: str = "",
    default: bool = False,
) -> bool:
    """Resolve bool: Runtime (incl. empty→default) > Deployment > Default."""
    raw, from_runtime = _runtime_raw(key)
    if from_runtime:
        return _parse_bool(raw, default=default)
    if env_name:
        env_val = _env_str(env_name)
        if env_val:
            return _parse_bool(env_val, default=default)
    if settings_attr:
        return bool(getattr(settings, settings_attr, default))
    return default


def get_int(key: str, *, env_name: str = "", settings_attr: str = "", default: int = 0) -> int:
    """Resolve int: Runtime (incl. empty→default) > Deployment > Default."""
    raw, from_runtime = _runtime_raw(key)
    if from_runtime:
        if raw == "":
            return default
        try:
            return int(raw)
        except ValueError:
            return default
    if env_name:
        env_val = _env_str(env_name)
        if env_val:
            try:
                return int(env_val)
            except ValueError:
                pass
    if settings_attr:
        try:
            return int(getattr(settings, settings_attr, default))
        except (TypeError, ValueError):
            return default
    return default


def get_str_list(key: str, *, settings_attr: str = "") -> list[str]:
    """Resolve list: Runtime (incl. explicit empty→[]) > Deployment > Default."""
    raw, from_runtime = _runtime_raw(key)
    if from_runtime:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            return [part.strip() for part in raw.split(",") if part.strip()]
        return []
    if settings_attr:
        val = getattr(settings, settings_attr, None)
        if isinstance(val, (list, tuple)):
            return [str(x).strip() for x in val if str(x).strip()]
    return []


def secret_configured(key: str, *, env_name: str = "", settings_attr: str = "") -> bool:
    row = _row(key)
    if row is not None:
        return bool(row.secret_ciphertext)
    if env_name and _env_str(env_name):
        return True
    if settings_attr and _settings_str(settings_attr):
        return True
    return False


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return f"{'*' * (len(value) - 4)}{value[-4:]}"


def set_value(
    *,
    key: str,
    value: str | None = None,
    secret: str | None = None,
    user: AbstractBaseUser | None = None,
    clear: bool = False,
) -> None:
    if clear:
        PlatformRuntimeSetting.objects.filter(key=key).delete()
        invalidate_runtime_settings_cache()
        return

    row, _created = PlatformRuntimeSetting.objects.get_or_create(key=key)
    update_fields = ["updated_at"]
    if user is not None:
        row.updated_by = user
        update_fields.append("updated_by")

    if secret is not None:
        secret = secret.strip()
        if secret == "":
            pass
        else:
            row.secret_ciphertext = encrypt_text(secret)
            row.value_text = ""
            update_fields.extend(["secret_ciphertext", "value_text"])
    elif value is not None:
        row.value_text = value.strip() if isinstance(value, str) else str(value)
        row.secret_ciphertext = ""
        update_fields.extend(["value_text", "secret_ciphertext"])

    row.save(update_fields=update_fields)
    invalidate_runtime_settings_cache()


def set_bool(key: str, value: bool, *, user: AbstractBaseUser | None = None) -> None:
    set_value(key=key, value="true" if value else "false", user=user)


def set_str_list(key: str, values: list[str], *, user: AbstractBaseUser | None = None) -> None:
    set_value(key=key, value=json.dumps(values), user=user)


# --- Effective getters used by overlay consumers ---


def enterprise_identity_enabled() -> bool:
    """Self-serve identity features require the platform extension (EE).

    Community empty socket: password login only. Email signup, email-code
    login, password reset mail, Google OAuth, and Turnstile stay off until
    a platform extension is loaded.
    """
    try:
        from common.extension_loader import extensions_enabled

        return bool(extensions_enabled())
    except Exception:  # pragma: no cover
        return False


def email_signup_enabled() -> bool:
    """Return whether anonymous email/password account creation is allowed."""
    if not enterprise_identity_enabled():
        return False
    return get_bool(
        KEY_IDENTITY_EMAIL_SIGNUP,
        env_name="HFL_EMAIL_SIGNUP_ENABLED",
        settings_attr="HFL_EMAIL_SIGNUP_ENABLED",
        default=False,
    )


def email_code_login_enabled() -> bool:
    """Return whether tenant users may sign in with an emailed code."""
    if not enterprise_identity_enabled():
        return False
    return get_bool(
        KEY_IDENTITY_EMAIL_CODE_LOGIN,
        env_name="HFL_EMAIL_CODE_LOGIN_ENABLED",
        settings_attr="HFL_EMAIL_CODE_LOGIN_ENABLED",
        default=False,
    )


def password_reset_available() -> bool:
    """Return whether self-serve email password reset is offered."""
    if not enterprise_identity_enabled():
        return False
    return email_delivery_configured()


def platform_ops_enabled() -> bool:
    return get_bool(
        KEY_IDENTITY_PLATFORM_OPS,
        env_name="HFL_PLATFORM_OPS_ENABLED",
        settings_attr="HFL_PLATFORM_OPS_ENABLED",
        default=True,
    )


def platform_ops_allowed_cidrs() -> list[str]:
    return get_str_list(KEY_IDENTITY_OPS_CIDRS, settings_attr="HFL_PLATFORM_OPS_ALLOWED_CIDRS")


def turnstile_enabled() -> bool:
    """Return the deployment-controlled Turnstile feature flag."""
    return bool(getattr(settings, "TURNSTILE_ENABLED", False))


def turnstile_site_key() -> str:
    return get_str(
        KEY_IDENTITY_TURNSTILE_SITE,
        env_name="TURNSTILE_SITE_KEY",
        settings_attr="TURNSTILE_SITE_KEY",
    )


def turnstile_secret_key() -> str:
    return get_secret(
        SECRET_KEY_TURNSTILE,
        env_name="TURNSTILE_SECRET_KEY",
        settings_attr="TURNSTILE_SECRET_KEY",
    )


def google_client_id() -> str:
    return get_str(
        KEY_IDENTITY_GOOGLE_CLIENT_ID,
        env_name="GOOGLE_CLIENT_ID",
        settings_attr="GOOGLE_CLIENT_ID",
    )


def google_client_secret() -> str:
    return get_secret(
        SECRET_KEY_GOOGLE,
        env_name="GOOGLE_CLIENT_SECRET",
        settings_attr="GOOGLE_CLIENT_SECRET",
    )


def google_oauth_enabled() -> bool:
    """Return whether Google OAuth is explicitly enabled and configured."""
    if not enterprise_identity_enabled():
        return False
    policy_enabled = get_bool(
        KEY_IDENTITY_GOOGLE_OAUTH,
        env_name="HFL_GOOGLE_OAUTH_ENABLED",
        settings_attr="HFL_GOOGLE_OAUTH_ENABLED",
        default=False,
    )
    return bool(policy_enabled and google_client_id() and google_client_secret())


def google_oauth_ready() -> bool:
    """Return whether one site-bound Google SocialApp is ready for login."""
    if not google_oauth_enabled():
        return False
    try:
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        site = Site.objects.get_current()
        apps = list(
            SocialApp.objects.filter(provider="google", sites=site)
            .distinct()
            .values_list("client_id", "secret")[:2]
        )
        return apps == [(google_client_id(), google_client_secret())]
    except (DatabaseError, ImproperlyConfigured, Site.DoesNotExist) as exc:
        logger.warning(
            "Google OAuth readiness check failed (%s)",
            type(exc).__name__,
        )
        return False


def _settings_email_connection_kwargs() -> dict[str, Any]:
    """Return process environment-backed Django mail settings without DB overlays."""
    return {
        "backend": _settings_str("EMAIL_BACKEND", CONSOLE_EMAIL_BACKEND),
        "host": _settings_str("EMAIL_HOST"),
        "port": int(getattr(settings, "EMAIL_PORT", 587) or 0),
        "username": _settings_str("EMAIL_HOST_USER"),
        "password": _settings_str("EMAIL_HOST_PASSWORD"),
        "use_tls": bool(getattr(settings, "EMAIL_USE_TLS", False)),
        "use_ssl": bool(getattr(settings, "EMAIL_USE_SSL", False)),
        "from_email": _settings_str(
            "DEFAULT_FROM_EMAIL",
            "HyperFileLens <noreply@hyperfilelens.local>",
        ),
    }


def _runtime_email_connection_kwargs() -> dict[str, Any]:
    return {
        "backend": get_str(
            KEY_EMAIL_BACKEND,
            settings_attr="EMAIL_BACKEND",
            default=CONSOLE_EMAIL_BACKEND,
        ),
        "host": get_str(KEY_EMAIL_HOST, settings_attr="EMAIL_HOST"),
        "port": get_int(
            KEY_EMAIL_PORT,
            settings_attr="EMAIL_PORT",
            default=587,
        ),
        "username": get_str(
            KEY_EMAIL_HOST_USER,
            settings_attr="EMAIL_HOST_USER",
        ),
        "password": get_secret(
            SECRET_KEY_EMAIL_PASSWORD,
            settings_attr="EMAIL_HOST_PASSWORD",
        ),
        "use_tls": get_bool(
            KEY_EMAIL_USE_TLS,
            settings_attr="EMAIL_USE_TLS",
            default=False,
        ),
        "use_ssl": get_bool(
            KEY_EMAIL_USE_SSL,
            settings_attr="EMAIL_USE_SSL",
            default=False,
        ),
        "from_email": get_str(
            KEY_EMAIL_FROM,
            settings_attr="DEFAULT_FROM_EMAIL",
            default="HyperFileLens <noreply@hyperfilelens.local>",
        ),
    }


def validate_email_connection_config(config: dict[str, Any]) -> str:
    """Return a stable configuration error, or an empty string when deliverable."""
    backend = str(config.get("backend") or "").strip()
    if backend in IN_MEMORY_EMAIL_BACKENDS:
        return ""
    if backend in {"", CONSOLE_EMAIL_BACKEND, DUMMY_EMAIL_BACKEND}:
        return "SMTP email delivery is not configured."
    if backend != SMTP_EMAIL_BACKEND:
        return "Unsupported email backend."

    missing = [
        label
        for label, value in (
            ("host", config.get("host")),
            ("username", config.get("username")),
            ("password", config.get("password")),
            ("from_email", config.get("from_email")),
        )
        if not str(value or "").strip()
    ]
    if missing:
        return f"SMTP configuration is missing: {', '.join(missing)}."
    try:
        port = int(config.get("port") or 0)
    except (TypeError, ValueError):
        return "SMTP port must be an integer between 1 and 65535."
    if port < 1 or port > 65535:
        return "SMTP port must be an integer between 1 and 65535."
    if bool(config.get("use_tls")) and bool(config.get("use_ssl")):
        return "SMTP SSL and STARTTLS cannot both be enabled."
    return ""


def _deployment_email_has_intent(config: dict[str, Any]) -> bool:
    backend = str(config.get("backend") or "").strip()
    return bool(
        backend == SMTP_EMAIL_BACKEND
        or str(config.get("host") or "").strip()
        or str(config.get("username") or "").strip()
        or str(config.get("password") or "").strip()
    )


def email_settings_managed_by_deployment() -> bool:
    """Whether Deployment SMTP is non-empty (UI hint only; does not change reads)."""
    return _deployment_email_has_intent(_settings_email_connection_kwargs())


def _runtime_email_configured() -> bool:
    return PlatformRuntimeSetting.objects.filter(key__in=EMAIL_RUNTIME_KEYS).exists()


def clear_email_runtime_settings(*, user: AbstractBaseUser | None = None) -> None:
    """Delete Runtime email overrides so Deployment → Default apply again."""
    _ = user
    PlatformRuntimeSetting.objects.filter(key__in=EMAIL_RUNTIME_KEYS).delete()
    invalidate_runtime_settings_cache()


def has_identity_runtime_override() -> bool:
    """Whether any identity PlatformRuntimeSetting row exists."""
    return PlatformRuntimeSetting.objects.filter(key__in=IDENTITY_RUNTIME_KEYS).exists()


def clear_identity_runtime_settings(*, user: AbstractBaseUser | None = None) -> None:
    """Delete Runtime identity overrides so Deployment → Default apply again."""
    _ = user
    PlatformRuntimeSetting.objects.filter(key__in=IDENTITY_RUNTIME_KEYS).delete()
    invalidate_runtime_settings_cache()


def email_connection_kwargs() -> dict[str, Any]:
    """Effective SMTP config: Runtime > Deployment > Default (per field)."""
    environment = _settings_email_connection_kwargs()
    managed = _deployment_email_has_intent(environment)
    config = _runtime_email_connection_kwargs()
    has_runtime = _runtime_email_configured()
    if has_runtime:
        source = "runtime"
    elif managed:
        source = "deployment"
    else:
        source = "default"
    return {
        **config,
        "source": source,
        "has_runtime_override": has_runtime,
        "managed_by_deployment": managed,
        "configuration_error": validate_email_connection_config(config),
    }


def email_delivery_configured() -> bool:
    return not bool(email_connection_kwargs()["configuration_error"])


def openai_api_key() -> str | None:
    val = get_secret(SECRET_KEY_OPENAI, env_name="OPENAI_API_KEY")
    return val or None


def openai_api_base() -> str:
    return get_str(
        KEY_AI_OPENAI_BASE,
        env_name="OPENAI_API_BASE",
        default="https://api.openai.com/v1/",
    )


def azure_openai_api_key() -> str | None:
    val = get_secret(SECRET_KEY_AZURE, env_name="AZURE_OPENAI_API_KEY")
    return val or None


def azure_openai_api_base() -> str | None:
    val = get_str(KEY_AI_AZURE_BASE, env_name="AZURE_OPENAI_API_BASE")
    return val or None


def gemini_api_key() -> str | None:
    val = get_secret(SECRET_KEY_GEMINI, env_name="GEMINI_API_KEY") or get_secret(
        SECRET_KEY_GEMINI,
        env_name="GOOGLE_API_KEY",
    )
    return val or None


def langfuse_public_key() -> str:
    return get_secret(SECRET_KEY_LANGFUSE_PUBLIC, env_name="LANGFUSE_PUBLIC_KEY")


def langfuse_secret_key() -> str:
    return get_secret(SECRET_KEY_LANGFUSE_SECRET, env_name="LANGFUSE_SECRET_KEY")


def langfuse_base_url() -> str:
    return get_str(KEY_AI_LANGFUSE_BASE, env_name="LANGFUSE_BASE_URL", default="http://localhost:3000")


def langfuse_enabled() -> bool:
    """Resolve Langfuse enabled: Runtime > Deployment > Default."""
    return get_bool(KEY_AI_LANGFUSE_ENABLED, env_name="LANGFUSE_ENABLED", default=False)


def _google_site_domain() -> str:
    """Return the canonical django.contrib.sites domain for the tenant URL."""
    from common.deploy.site import tenant_public_url

    public_url = tenant_public_url()
    parsed = urlsplit(public_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("FRONTEND_URL must be an absolute HTTP(S) URL")
    return parsed.netloc


def sync_google_social_app() -> GoogleSocialAppSyncResult:
    """Converge runtime Google OAuth credentials into one site-bound SocialApp."""
    client_id = google_client_id()
    secret = google_client_secret()
    if not client_id or not secret:
        return GoogleSocialAppSyncResult(configured=False)

    from allauth.socialaccount.models import SocialApp
    from django.contrib.sites.models import Site

    site_domain = _google_site_domain()
    site_id = int(getattr(settings, "SITE_ID", 1))
    with transaction.atomic():
        site, _created = Site.objects.select_for_update().get_or_create(
            pk=site_id,
            defaults={"domain": site_domain, "name": site_domain},
        )
        site_updates: list[str] = []
        if site.domain != site_domain:
            site.domain = site_domain
            site_updates.append("domain")
        if site.name != site_domain:
            site.name = site_domain
            site_updates.append("name")
        if site_updates:
            site.save(update_fields=site_updates)

        existing = list(
            SocialApp.objects.select_for_update()
            .filter(provider="google")
            .order_by("pk")
        )
        app = next(
            (candidate for candidate in existing if candidate.client_id == client_id),
            existing[0] if existing else None,
        )
        if app is None:
            app = SocialApp(provider="google")
        app.name = "Google"
        app.client_id = client_id
        app.secret = secret
        app.save()
        app.sites.set([site])

        duplicate_ids = [
            candidate.pk
            for candidate in existing
            if candidate.pk is not None and candidate.pk != app.pk
        ]
        if duplicate_ids:
            SocialApp.objects.filter(pk__in=duplicate_ids).delete()

    return GoogleSocialAppSyncResult(
        configured=True,
        app_id=app.pk,
        site_domain=site_domain,
        removed_duplicates=len(duplicate_ids),
    )
