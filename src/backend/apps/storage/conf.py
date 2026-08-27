"""Storage defaults and runtime settings."""

import os

from django.core.exceptions import ImproperlyConfigured


CONFIG_KEY_RETENTION = "backup.retention.default"
CONFIG_KEY_FILTERS = "backup.filters.default"

DEFAULT_RETENTION = {
    "type": "gfs",
    "daily": 7,
    "weekly": 4,
    "monthly": 12,
}

DEFAULT_FILTERS = {
    "exclude": ["/tmp", "/var/cache"],
    "include": [],
}


REPOSITORY_HEALTH_INTERVAL_ENV = "STORAGE_REPOSITORY_HEALTH_INTERVAL_SECONDS"
DEFAULT_REPOSITORY_HEALTH_INTERVAL_SECONDS = 300
MIN_REPOSITORY_HEALTH_INTERVAL_SECONDS = 60

CELERY_WORKER_CONCURRENCY_ENV = "CELERY_WORKER_CONCURRENCY"
BACKGROUND_STORAGE_CONCURRENCY_ENV = "CELERY_BACKGROUND_STORAGE_CONCURRENCY"
DEFAULT_CELERY_WORKER_CONCURRENCY = 4

KOPIA_CONFIG_LOCK_TIMEOUT_ENV = "HFL_KOPIA_CONFIG_LOCK_TIMEOUT_SECONDS"
DEFAULT_KOPIA_CONFIG_LOCK_TIMEOUT_SECONDS = 10

PROVIDER_CATALOG_MAX_BYTES_ENV = "STORAGE_PROVIDER_CATALOG_MAX_BYTES"
PROVIDER_CATALOG_MAX_DEPTH_ENV = "STORAGE_PROVIDER_CATALOG_MAX_DEPTH"
PROVIDER_CATALOG_MAX_PROVIDERS_ENV = "STORAGE_PROVIDER_CATALOG_MAX_PROVIDERS"
PROVIDER_CATALOG_MAX_REGIONS_ENV = "STORAGE_PROVIDER_CATALOG_MAX_REGIONS"
PROVIDER_CATALOG_REVIEW_TOKEN_TTL_ENV = (
    "STORAGE_PROVIDER_CATALOG_REVIEW_TOKEN_TTL_SECONDS"
)
PROVIDER_VALIDATION_CREDENTIAL_TTL_ENV = (
    "STORAGE_PROVIDER_VALIDATION_CREDENTIAL_TTL_SECONDS"
)
PROVIDER_VALIDATION_RUN_TTL_ENV = "STORAGE_PROVIDER_VALIDATION_RUN_TTL_SECONDS"
PROVIDER_VALIDATION_RETENTION_ENV = (
    "STORAGE_PROVIDER_VALIDATION_RETENTION_SECONDS"
)
PROVIDER_VALIDATION_REGION_TIMEOUT_ENV = (
    "STORAGE_PROVIDER_VALIDATION_REGION_TIMEOUT_SECONDS"
)
PROVIDER_VALIDATION_ALLOW_PROXY_FAKE_IP_ENV = (
    "STORAGE_PROVIDER_VALIDATION_ALLOW_PROXY_FAKE_IP"
)

DEFAULT_PROVIDER_CATALOG_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_PROVIDER_CATALOG_MAX_DEPTH = 16
DEFAULT_PROVIDER_CATALOG_MAX_PROVIDERS = 32
DEFAULT_PROVIDER_CATALOG_MAX_REGIONS = 256
DEFAULT_PROVIDER_CATALOG_REVIEW_TOKEN_TTL_SECONDS = 15 * 60
DEFAULT_PROVIDER_VALIDATION_CREDENTIAL_TTL_SECONDS = 6 * 60 * 60
DEFAULT_PROVIDER_VALIDATION_RUN_TTL_SECONDS = 4 * 60 * 60
DEFAULT_PROVIDER_VALIDATION_RETENTION_SECONDS = 24 * 60 * 60
DEFAULT_PROVIDER_VALIDATION_REGION_TIMEOUT_SECONDS = 3 * 60


def repository_health_interval_seconds() -> int:
    raw = os.getenv(
        REPOSITORY_HEALTH_INTERVAL_ENV,
        str(DEFAULT_REPOSITORY_HEALTH_INTERVAL_SECONDS),
    ).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(
            f"{REPOSITORY_HEALTH_INTERVAL_ENV} must be an integer."
        ) from exc
    if value < MIN_REPOSITORY_HEALTH_INTERVAL_SECONDS:
        raise ImproperlyConfigured(
            f"{REPOSITORY_HEALTH_INTERVAL_ENV} must be at least "
            f"{MIN_REPOSITORY_HEALTH_INTERVAL_SECONDS} seconds."
        )
    return value


def background_storage_concurrency() -> int:
    """Return the shared Controller background-storage execution budget."""

    worker_concurrency = _positive_int_setting(
        CELERY_WORKER_CONCURRENCY_ENV,
        DEFAULT_CELERY_WORKER_CONCURRENCY,
    )
    if worker_concurrency < 2:
        raise ImproperlyConfigured(
            f"{CELERY_WORKER_CONCURRENCY_ENV} must be at least 2 when "
            "background storage work is enabled."
        )
    default_background = max(1, worker_concurrency // 2)
    background_concurrency = _positive_int_setting(
        BACKGROUND_STORAGE_CONCURRENCY_ENV,
        default_background,
    )
    if background_concurrency >= worker_concurrency:
        raise ImproperlyConfigured(
            f"{BACKGROUND_STORAGE_CONCURRENCY_ENV} must be less than "
            f"{CELERY_WORKER_CONCURRENCY_ENV}: background="
            f"{background_concurrency}, worker={worker_concurrency}."
        )
    return background_concurrency


def kopia_config_lock_timeout_seconds() -> int:
    """Return the finite wait for one Controller-local Kopia config lock."""

    return _positive_int_setting(
        KOPIA_CONFIG_LOCK_TIMEOUT_ENV,
        DEFAULT_KOPIA_CONFIG_LOCK_TIMEOUT_SECONDS,
    )


def _positive_int_setting(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured(f"{name} must be an integer.") from exc
    if value < 1:
        raise ImproperlyConfigured(f"{name} must be at least 1.")
    return value


def provider_catalog_limits() -> dict[str, int]:
    return {
        "max_bytes": _positive_int_setting(
            PROVIDER_CATALOG_MAX_BYTES_ENV,
            DEFAULT_PROVIDER_CATALOG_MAX_BYTES,
        ),
        "max_depth": _positive_int_setting(
            PROVIDER_CATALOG_MAX_DEPTH_ENV,
            DEFAULT_PROVIDER_CATALOG_MAX_DEPTH,
        ),
        "max_providers": _positive_int_setting(
            PROVIDER_CATALOG_MAX_PROVIDERS_ENV,
            DEFAULT_PROVIDER_CATALOG_MAX_PROVIDERS,
        ),
        "max_regions": _positive_int_setting(
            PROVIDER_CATALOG_MAX_REGIONS_ENV,
            DEFAULT_PROVIDER_CATALOG_MAX_REGIONS,
        ),
    }


def provider_catalog_review_token_ttl_seconds() -> int:
    return _positive_int_setting(
        PROVIDER_CATALOG_REVIEW_TOKEN_TTL_ENV,
        DEFAULT_PROVIDER_CATALOG_REVIEW_TOKEN_TTL_SECONDS,
    )


def provider_validation_credential_ttl_seconds() -> int:
    return _positive_int_setting(
        PROVIDER_VALIDATION_CREDENTIAL_TTL_ENV,
        DEFAULT_PROVIDER_VALIDATION_CREDENTIAL_TTL_SECONDS,
    )


def provider_validation_run_ttl_seconds() -> int:
    return min(
        _positive_int_setting(
            PROVIDER_VALIDATION_RUN_TTL_ENV,
            DEFAULT_PROVIDER_VALIDATION_RUN_TTL_SECONDS,
        ),
        provider_validation_credential_ttl_seconds(),
    )


def provider_validation_retention_seconds() -> int:
    return _positive_int_setting(
        PROVIDER_VALIDATION_RETENTION_ENV,
        DEFAULT_PROVIDER_VALIDATION_RETENTION_SECONDS,
    )


def provider_validation_region_timeout_seconds() -> int:
    return _positive_int_setting(
        PROVIDER_VALIDATION_REGION_TIMEOUT_ENV,
        DEFAULT_PROVIDER_VALIDATION_REGION_TIMEOUT_SECONDS,
    )


def provider_validation_allow_proxy_fake_ip() -> bool:
    """Allow reserved proxy Fake-IP ranges only when explicitly enabled."""

    return os.getenv(PROVIDER_VALIDATION_ALLOW_PROXY_FAKE_IP_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
