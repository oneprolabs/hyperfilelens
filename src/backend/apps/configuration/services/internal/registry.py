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


def _spec(
    *,
    key: str,
    category: str,
    value_type: str,
    description: str,
    owning_app: str,
) -> ConfigKeySpec:
    return ConfigKeySpec(
        key=key,
        category=category,
        value_type=value_type,
        description=description,
        owning_app=owning_app,
    )


def all_key_specs() -> tuple[ConfigKeySpec, ...]:
    from apps.iam import conf as iam_conf
    from apps.insight import conf as insight_conf
    from apps.storage import conf as storage_conf

    return (
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
            key=iam_conf.CONFIG_KEY_REGISTRATION_TOKEN_EXPIRY_HOURS,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Registration token expiry (hours)",
            owning_app="iam",
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_PASSWORD_RESET_TIMEOUT,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Password reset timeout (seconds)",
            owning_app="iam",
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_REGISTRATION_CODE_MINUTES,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Registration verification code TTL (minutes)",
            owning_app="iam",
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_PASSWORD_RESET_CODE_MINUTES,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Password reset verification code TTL (minutes)",
            owning_app="iam",
        ),
        _spec(
            key=iam_conf.CONFIG_KEY_LOGIN_CODE_MINUTES,
            category="iam",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Email login verification code TTL (minutes)",
            owning_app="iam",
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LLM_PROVIDER,
            category="insight",
            value_type=GlobalConfig.ValueType.STRING,
            description="Active LLM provider id",
            owning_app="insight",
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LLM_OUTPUT_LANGUAGE,
            category="insight",
            value_type=GlobalConfig.ValueType.STRING,
            description="LLM output language",
            owning_app="insight",
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_OPENAI_MODEL,
            category="insight",
            value_type=GlobalConfig.ValueType.STRING,
            description="OpenAI model name",
            owning_app="insight",
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LANGFUSE_ENABLED,
            category="observability",
            value_type=GlobalConfig.ValueType.BOOLEAN,
            description="Override Langfuse enabled flag",
            owning_app="insight",
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LANGFUSE_SAMPLE_RATE,
            category="observability",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Langfuse trace sample rate",
            owning_app="insight",
        ),
        _spec(
            key=insight_conf.CONFIG_KEY_LANGFUSE_TIMEOUT,
            category="observability",
            value_type=GlobalConfig.ValueType.NUMBER,
            description="Langfuse client timeout (seconds)",
            owning_app="insight",
        ),
    )


def registry_by_key() -> dict[str, ConfigKeySpec]:
    return {spec.key: spec for spec in all_key_specs()}
