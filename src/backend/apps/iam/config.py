"""Runtime IAM policy (GlobalConfig with app defaults)."""

from apps.configuration.constants import NOT_FOUND
from apps.configuration.selectors.interface import get_config
from apps.iam import conf


def get_registration_token_expiry_minutes(*, tenant_key: str | None = None) -> int:
    """Return registration-token TTL in minutes (legacy hours configs are converted)."""
    minutes = get_config(
        conf.CONFIG_KEY_REGISTRATION_TOKEN_EXPIRY_MINUTES,
        tenant_key=tenant_key,
        default=NOT_FOUND,
    )
    if minutes is not NOT_FOUND and minutes is not None:
        return max(1, int(minutes))
    hours = get_config(
        conf.CONFIG_KEY_REGISTRATION_TOKEN_EXPIRY_HOURS,
        tenant_key=tenant_key,
        default=conf.DEFAULT_REGISTRATION_TOKEN_EXPIRY_HOURS,
    )
    return max(1, int(hours) * 60)


def get_registration_token_expiry_hours(*, tenant_key: str | None = None) -> int:
    """Compatibility wrapper; prefer ``get_registration_token_expiry_minutes``."""
    return max(1, (get_registration_token_expiry_minutes(tenant_key=tenant_key) + 59) // 60)


def get_password_reset_timeout_minutes(*, tenant_key: str | None = None) -> int:
    """Return password-reset token TTL in minutes (legacy seconds configs are converted)."""
    minutes = get_config(
        conf.CONFIG_KEY_PASSWORD_RESET_TIMEOUT_MINUTES,
        tenant_key=tenant_key,
        default=NOT_FOUND,
    )
    if minutes is not NOT_FOUND and minutes is not None:
        return max(1, int(minutes))
    seconds = get_config(
        conf.CONFIG_KEY_PASSWORD_RESET_TIMEOUT,
        tenant_key=tenant_key,
        default=conf.DEFAULT_PASSWORD_RESET_TIMEOUT_SECONDS,
    )
    return max(1, int(seconds) // 60)


def get_password_reset_timeout_seconds(*, tenant_key: str | None = None) -> int:
    """Compatibility wrapper; prefer ``get_password_reset_timeout_minutes``."""
    return max(60, get_password_reset_timeout_minutes(tenant_key=tenant_key) * 60)


def get_registration_verification_code_minutes(*, tenant_key: str | None = None) -> int:
    value = get_config(
        conf.CONFIG_KEY_REGISTRATION_CODE_MINUTES,
        tenant_key=tenant_key,
        default=conf.DEFAULT_REGISTRATION_VERIFICATION_CODE_MINUTES,
    )
    return int(value)


def get_password_reset_verification_code_minutes(*, tenant_key: str | None = None) -> int:
    value = get_config(
        conf.CONFIG_KEY_PASSWORD_RESET_CODE_MINUTES,
        tenant_key=tenant_key,
        default=conf.DEFAULT_PASSWORD_RESET_VERIFICATION_CODE_MINUTES,
    )
    return int(value)


def get_login_verification_code_minutes(*, tenant_key: str | None = None) -> int:
    value = get_config(
        conf.CONFIG_KEY_LOGIN_CODE_MINUTES,
        tenant_key=tenant_key,
        default=conf.DEFAULT_LOGIN_VERIFICATION_CODE_MINUTES,
    )
    return int(value)
