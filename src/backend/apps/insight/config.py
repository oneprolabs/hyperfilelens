"""Runtime LLM configuration (secrets from deploy, policy from GlobalConfig)."""

from __future__ import annotations

from typing import Any

from apps.configuration.selectors.interface import get_config
from apps.insight import conf, deploy


def get_llm_provider(*, tenant_key: str | None = None) -> str:
    value = get_config(
        conf.CONFIG_KEY_LLM_PROVIDER,
        tenant_key=tenant_key,
        default=conf.DEFAULT_LLM_PROVIDER,
    )
    return str(value).lower()


def get_llm_output_language(*, tenant_key: str | None = None) -> str:
    value = get_config(
        conf.CONFIG_KEY_LLM_OUTPUT_LANGUAGE,
        tenant_key=tenant_key,
        default=conf.DEFAULT_LLM_OUTPUT_LANGUAGE,
    )
    return str(value)


def _numeric(
    key: str,
    *,
    tenant_key: str | None,
    default: int | float,
    cast: type[int] | type[float],
) -> int | float:
    value = get_config(key, tenant_key=tenant_key, default=default)
    return cast(value)


def get_openai_config(*, tenant_key: str | None = None) -> dict[str, Any]:
    return {
        "api_base": deploy.openai_api_base(),
        "api_key": deploy.openai_api_key(),
        "model": get_config(
            conf.CONFIG_KEY_OPENAI_MODEL,
            tenant_key=tenant_key,
            default=conf.DEFAULT_OPENAI_MODEL,
        ),
        "max_tokens": _numeric(
            conf.CONFIG_KEY_OPENAI_MAX_TOKENS,
            tenant_key=tenant_key,
            default=conf.DEFAULT_OPENAI_MAX_TOKENS,
            cast=int,
        ),
        "temperature": _numeric(
            conf.CONFIG_KEY_OPENAI_TEMPERATURE,
            tenant_key=tenant_key,
            default=conf.DEFAULT_OPENAI_TEMPERATURE,
            cast=float,
        ),
    }


def get_azure_openai_config(*, tenant_key: str | None = None) -> dict[str, Any]:
    return {
        "api_base": deploy.azure_openai_api_base(),
        "api_key": deploy.azure_openai_api_key(),
        "deployment": get_config(
            conf.CONFIG_KEY_AZURE_DEPLOYMENT,
            tenant_key=tenant_key,
            default=conf.DEFAULT_AZURE_DEPLOYMENT,
        ),
        "api_version": get_config(
            conf.CONFIG_KEY_AZURE_API_VERSION,
            tenant_key=tenant_key,
            default=conf.DEFAULT_AZURE_API_VERSION,
        ),
        "max_tokens": _numeric(
            conf.CONFIG_KEY_AZURE_MAX_TOKENS,
            tenant_key=tenant_key,
            default=conf.DEFAULT_AZURE_MAX_TOKENS,
            cast=int,
        ),
        "temperature": _numeric(
            conf.CONFIG_KEY_AZURE_TEMPERATURE,
            tenant_key=tenant_key,
            default=conf.DEFAULT_AZURE_TEMPERATURE,
            cast=float,
        ),
    }


def get_gemini_config(*, tenant_key: str | None = None) -> dict[str, Any]:
    return {
        "api_key": deploy.gemini_api_key(),
        "model": get_config(
            conf.CONFIG_KEY_GEMINI_MODEL,
            tenant_key=tenant_key,
            default=conf.DEFAULT_GEMINI_MODEL,
        ),
        "max_tokens": _numeric(
            conf.CONFIG_KEY_GEMINI_MAX_TOKENS,
            tenant_key=tenant_key,
            default=conf.DEFAULT_GEMINI_MAX_TOKENS,
            cast=int,
        ),
        "temperature": _numeric(
            conf.CONFIG_KEY_GEMINI_TEMPERATURE,
            tenant_key=tenant_key,
            default=conf.DEFAULT_GEMINI_TEMPERATURE,
            cast=float,
        ),
    }


def get_langfuse_settings(*, tenant_key: str | None = None) -> dict[str, Any]:
    """Langfuse client settings (secrets/enabled from PRS deploy; sample/timeout from GC).

    ``enabled``: optional Tenant/GC Runtime override via get_config (no Deployment
    env on that key), else PlatformRuntimeSetting R>D>Def (``LANGFUSE_ENABLED`` /
    Console ``ai.langfuse_enabled``).
    """
    enabled_override = get_config(
        conf.CONFIG_KEY_LANGFUSE_ENABLED,
        tenant_key=tenant_key,
        default=None,
    )
    if enabled_override is None:
        enabled = deploy.langfuse_enabled_by_env()
    else:
        enabled = bool(enabled_override)

    sample_rate = get_config(
        conf.CONFIG_KEY_LANGFUSE_SAMPLE_RATE,
        tenant_key=tenant_key,
        default=conf.DEFAULT_LANGFUSE_SAMPLE_RATE,
    )
    timeout = get_config(
        conf.CONFIG_KEY_LANGFUSE_TIMEOUT,
        tenant_key=tenant_key,
        default=conf.DEFAULT_LANGFUSE_TIMEOUT_SECONDS,
    )

    return {
        "enabled": enabled,
        "public_key": deploy.langfuse_public_key(),
        "secret_key": deploy.langfuse_secret_key(),
        "host": deploy.langfuse_host(),
        "sample_rate": float(sample_rate),
        "timeout": int(timeout),
    }


def get_active_llm_config(*, tenant_key: str | None = None) -> dict[str, Any]:
    """Return provider config dict for the configured LLM_PROVIDER."""
    provider = get_llm_provider(tenant_key=tenant_key)
    if provider == "openai":
        return get_openai_config(tenant_key=tenant_key)
    if provider in ("azure_openai", "azure"):
        return get_azure_openai_config(tenant_key=tenant_key)
    if provider == "gemini":
        return get_gemini_config(tenant_key=tenant_key)
    return get_azure_openai_config(tenant_key=tenant_key)
