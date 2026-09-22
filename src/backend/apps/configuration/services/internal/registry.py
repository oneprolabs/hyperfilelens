"""Registry of known configuration keys owned by domain apps."""

from __future__ import annotations

from dataclasses import dataclass

from apps.configuration.models import GlobalConfig


@dataclass(frozen=True)
class ConfigKeySpec:
    key: str
    category: str
    value_type: str
    description: str
    owning_app: str
    # Deployment layer: first non-empty env, else first non-empty settings attr.
    env_names: tuple[str, ...] = ()
    settings_attrs: tuple[str, ...] = ()
    runtime_allowed: bool = True
    tenant_capable: bool = False


def _spec(
    *,
    key: str,
    category: str,
    value_type: str,
    description: str,
    owning_app: str,
    env_names: tuple[str, ...] = (),
    settings_attrs: tuple[str, ...] = (),
    runtime_allowed: bool = True,
    tenant_capable: bool = False,
) -> ConfigKeySpec:
    return ConfigKeySpec(
        key=key,
        category=category,
        value_type=value_type,
        description=description,
        owning_app=owning_app,
        env_names=env_names,
        settings_attrs=settings_attrs,
        runtime_allowed=runtime_allowed,
        tenant_capable=tenant_capable,
    )


def all_key_specs() -> tuple[ConfigKeySpec, ...]:
    from apps.iam import conf as iam_conf
    from apps.insight import conf as insight_conf
    from apps.instance_settings import conf as instance_settings_conf
    from apps.storage import conf as storage_conf

    return (
        _spec(
            key=instance_settings_conf.CONFIG_KEY_EXTERNAL_ACCESS_URL,
            category="deployment",
            value_type=GlobalConfig.ValueType.STRING,
            description="Instance external access URL",
            owning_app="instance_settings",
            env_names=("HFL_EXTERNAL_ACCESS_URL",),
            settings_attrs=("HFL_EXTERNAL_ACCESS_URL", "FRONTEND_URL"),
            tenant_capable=False,
        ),
        _spec(
            key=storage_conf.CONFIG_KEY_RETENTION,
            category="backup",
            value_type=GlobalConfig.ValueType.OBJECT,
            description="Default GFS retention template",
            owning_app="storage",
        ),
        _spec(
            key=storage_conf.CONFIG_KEY_FILTERS,
            category="backup",
            value_type=GlobalConfig.ValueType.OBJECT,
            description="Default include/exclude template",
            owning_app="storage",
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_REGISTRATION_TOKEN_EXPIRY_MINUTES,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Registration token expiry (minutes)",
            owning_app="iam",
            env_names=("HFL_IAM_REGISTRATION_TOKEN_EXPIRY_MINUTES",),
            tenant_capable=True,
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_REGISTRATION_TOKEN_EXPIRY_HOURS,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Registration token expiry (hours, legacy)",
            owning_app="iam",
            env_names=("HFL_IAM_REGISTRATION_TOKEN_EXPIRY_HOURS",),
            tenant_capable=True,
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_PASSWORD_RESET_TIMEOUT_MINUTES,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Password reset timeout (minutes)",
            owning_app="iam",
            env_names=("HFL_IAM_PASSWORD_RESET_TIMEOUT_MINUTES",),
            tenant_capable=True,
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_PASSWORD_RESET_TIMEOUT,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Password reset timeout (seconds, legacy)",
            owning_app="iam",
            env_names=("HFL_IAM_PASSWORD_RESET_TIMEOUT_SECONDS",),
            tenant_capable=True,
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_REGISTRATION_CODE_MINUTES,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Registration verification code TTL (minutes)",
            owning_app="iam",
            env_names=("HFL_IAM_REGISTRATION_CODE_MINUTES",),
            tenant_capable=True,
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_PASSWORD_RESET_CODE_MINUTES,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Password reset verification code TTL (minutes)",
            owning_app="iam",
            env_names=("HFL_IAM_PASSWORD_RESET_CODE_MINUTES",),
            tenant_capable=True,
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Email login verification code TTL (minutes)",
            owning_app="iam",
            env_names=("HFL_IAM_LOGIN_CODE_MINUTES",),
            tenant_capable=True,
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LLM_PROVIDER,
            category="insight",
            value_type=GlobalConfig.ValueType.STRING,
            description="Active LLM provider id",
            owning_app="insight",
            env_names=("HFL_INSIGHT_LLM_PROVIDER",),
            tenant_capable=True,
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LLM_OUTPUT_LANGUAGE,
            category="insight",
            value_type=GlobalConfig.ValueType.STRING,
            description="LLM output language",
            owning_app="insight",
            env_names=("HFL_INSIGHT_LLM_OUTPUT_LANGUAGE",),
            tenant_capable=True,
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_OPENAI_MODEL,
            category="insight",
            value_type=GlobalConfig.ValueType.STRING,
            description="OpenAI model name",
            owning_app="insight",
            env_names=("HFL_INSIGHT_OPENAI_MODEL",),
            tenant_capable=True,
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LANGFUSE_ENABLED,
            category="observability",
            value_type=GlobalConfig.ValueType.BOOLEAN,
            description=(
                "Tenant/GlobalConfig Runtime override for Langfuse enabled. "
                "Platform Deployment (LANGFUSE_ENABLED) and Console AI Runtime "
                "resolve via PlatformRuntimeSetting (ai.langfuse_enabled), not "
                "this Deployment env binding — avoids dual-store conflict."
            ),
            owning_app="insight",
            env_names=(),
            tenant_capable=True,
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LANGFUSE_SAMPLE_RATE,
            category="observability",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Langfuse trace sample rate",
            owning_app="insight",
            env_names=("LANGFUSE_SAMPLE_RATE",),
            tenant_capable=True,
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LANGFUSE_TIMEOUT,
            category="observability",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Langfuse client timeout (seconds)",
            owning_app="insight",
            env_names=("LANGFUSE_TIMEOUT_SECONDS",),
            tenant_capable=True,
        ),
    )


def registry_by_key() -> dict[str, ConfigKeySpec]:
    return {spec.key: spec for spec in all_key_specs()}
