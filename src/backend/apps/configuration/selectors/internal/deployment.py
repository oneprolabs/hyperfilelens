"""Deployment-layer resolution for registered GlobalConfig keys."""

from __future__ import annotations

import os
from typing import Any

from django.conf import settings

from apps.configuration.constants import NOT_FOUND
from apps.configuration.models import GlobalConfig
from apps.configuration.services.internal.registry import ConfigKeySpec, registry_by_key


def _env_raw(name: str) -> str | None:
    if name not in os.environ:
        return None
    return str(os.environ.get(name) or "").strip()


def _coerce_env(raw: str, value_type: str) -> Any:
    if value_type == GlobalConfig.ValueType.BOOLEAN:
        return raw.lower() in {"1", "true", "yes", "on"}
    if value_type == GlobalConfig.ValueType.NUMBER:
        if "." in raw:
            return float(raw)
        return int(raw)
    if value_type == GlobalConfig.ValueType.ARRAY:
        return [part.strip() for part in raw.split(",") if part.strip()]
    # OBJECT is not supported via plain env strings.
    if value_type == GlobalConfig.ValueType.OBJECT:
        return NOT_FOUND
    return raw


def _settings_value(attr: str) -> Any:
    if not hasattr(settings, attr):
        return NOT_FOUND
    val = getattr(settings, attr)
    if val is None:
        return NOT_FOUND
    if isinstance(val, str) and not val.strip():
        return NOT_FOUND
    if isinstance(val, str):
        return val.strip().rstrip("/") if attr in {
            "HFL_EXTERNAL_ACCESS_URL",
            "FRONTEND_URL",
        } else val.strip()
    return val


def resolve_deployment_value(*, config_key: str) -> Any:
    """
    Resolve Deployment for a registered key.

    Returns ``NOT_FOUND`` when no env/settings value is set (empty = unset).
    Unknown keys have no Deployment layer.
    """
    spec: ConfigKeySpec | None = registry_by_key().get(config_key)
    if spec is None:
        return NOT_FOUND

    for env_name in spec.env_names:
        raw = _env_raw(env_name)
        if raw is None or raw == "":
            continue
        try:
            coerced = _coerce_env(raw, spec.value_type)
        except (TypeError, ValueError):
            continue
        if coerced is NOT_FOUND:
            continue
        return coerced

    for attr in spec.settings_attrs:
        # Prefer env-backed settings that were not already consumed via env_names.
        if attr in spec.env_names:
            # Already attempted via os.environ; settings may still hold a value
            # when Django loaded defaults — only use when env was absent.
            if attr in os.environ:
                continue
        value = _settings_value(attr)
        if value is NOT_FOUND:
            continue
        if spec.value_type == GlobalConfig.ValueType.BOOLEAN:
            return bool(value)
        if spec.value_type == GlobalConfig.ValueType.NUMBER:
            try:
                if isinstance(value, (int, float)):
                    return value
                text = str(value).strip()
                return float(text) if "." in text else int(text)
            except (TypeError, ValueError):
                continue
        return value

    return NOT_FOUND
