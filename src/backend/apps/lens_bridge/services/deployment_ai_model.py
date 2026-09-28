"""Reconcile deployment-provided platform AI model configurations.

Deployment configuration is an input to reconciliation, not a separate model
lifecycle. Models created by deployment and models created in the Admin Console
share the same HFL/SourceLens model resources and remain user-manageable.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit, urlunsplit

from django.db import transaction

from apps.iam.models import Organization
from apps.lens_bridge.services import org_models, platform_lens, provisioning, sl_client

AiModelRole = Literal["agent", "multimodal"]


class DeploymentAiModelConfigurationError(ValueError):
    """Raised when deployment input is incomplete or unsafe."""


@dataclass(frozen=True)
class DeploymentAiModelConfig:
    """Validated deployment input for one OpenAI-compatible model."""

    provider: str
    model_id: str
    display_name: str
    api_base: str
    api_key: str
    supports_vision: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "DeploymentAiModelConfig":
        provider = str(values.get("provider") or "").strip().lower()
        model_id = str(values.get("model_id") or "").strip()
        display_name = str(values.get("display_name") or "").strip()
        api_base = str(values.get("api_base") or "").strip()
        api_key = str(values.get("api_key") or "").strip()
        if (
            not provider
            or len(provider) > 64
            or not re.fullmatch(r"[a-z0-9_]+", provider)
        ):
            raise DeploymentAiModelConfigurationError("provider is required")
        if (
            not model_id
            or len(model_id) > 255
            or "\n" in model_id
            or "\r" in model_id
        ):
            raise DeploymentAiModelConfigurationError("model_id is required")
        if not display_name or "\n" in display_name or "\r" in display_name:
            raise DeploymentAiModelConfigurationError("display_name is required")
        if (
            not api_key
            or len(api_key) > 4096
            or "\n" in api_key
            or "\r" in api_key
        ):
            raise DeploymentAiModelConfigurationError("api_key is required")
        try:
            parsed = urlsplit(api_base)
            parsed.port
        except ValueError as exc:
            raise DeploymentAiModelConfigurationError("api_base is invalid") from exc
        if (
            len(api_base) > 2048
            or
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or re.search(r"\s", parsed.netloc)
        ):
            raise DeploymentAiModelConfigurationError(
                "api_base must be an HTTPS URL without credentials, query, or fragment"
            )
        supports_vision = values.get("supports_vision")
        if isinstance(supports_vision, str):
            supports_vision = supports_vision.strip().lower() in {
                "true",
                "1",
                "yes",
                "on",
            }
        return cls(
            provider=provider,
            model_id=model_id,
            display_name=display_name[:160],
            api_base=urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")),
            api_key=api_key,
            supports_vision=bool(supports_vision),
        )


@dataclass(frozen=True)
class DeploymentAiModelResult:
    """Sanitized result suitable for management-command output."""

    action: Literal["created", "updated"]
    connectivity_ok: bool
    applied: bool = True


def _source_lens_payload(
    config: DeploymentAiModelConfig,
) -> dict[str, Any]:
    model_config: dict[str, Any] = {
        "model": config.model_id,
        "api_base": config.api_base,
        "api_key": config.api_key,
    }
    if config.supports_vision:
        model_config["supports_vision"] = True
    return {
        "provider": config.provider,
        "config": model_config,
        "is_active": True,
    }


def _remote_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        for key in ("results", "items", "data"):
            value = raw.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = _remote_items(value)
                if nested:
                    return nested
    return []


def _normalized_api_base(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _model_matches_config(
    data: dict[str, Any] | None,
    config: DeploymentAiModelConfig,
) -> bool:
    if not isinstance(data, dict):
        return False
    model_config = data.get("config")
    if not isinstance(model_config, dict):
        return False
    return (
        str(data.get("provider") or "").strip().lower() == config.provider
        and str(model_config.get("model") or "").strip() == config.model_id
        and _normalized_api_base(model_config.get("api_base"))
        == _normalized_api_base(config.api_base)
    )


def _list_source_lens_models() -> list[dict[str, Any]]:
    return _remote_items(sl_client.request_json("GET", "/api/v1/admin/llm-config/"))


def _find_existing_model(
    *,
    org: Organization,
    config: DeploymentAiModelConfig,
    role: AiModelRole,
) -> dict[str, Any] | None:
    rows = [row for row in _list_source_lens_models() if _model_matches_config(row, config)]
    if not rows:
        return None
    defaults = provisioning.get_or_create_org_link(org)
    preferred_ref = (
        defaults.default_multimodal_model_ref
        if role == "multimodal"
        else defaults.default_agent_model_ref
    )
    preferred = str(preferred_ref or "")
    if preferred:
        for row in rows:
            if str(row.get("uuid") or "") == preferred:
                return row
    return rows[0]


def _test_connection(config: DeploymentAiModelConfig) -> bool:
    try:
        data = sl_client.request_json(
            "POST",
            "/api/v1/admin/llm-config/test/",
            json_body=_source_lens_payload(config),
        )
    except sl_client.LensBridgeError:
        return False
    return isinstance(data, dict) and (data.get("ok") is True or data.get("success") is True)


@transaction.atomic
def ensure_platform_ai_model(
    config: DeploymentAiModelConfig,
    *,
    role: AiModelRole = "agent",
) -> DeploymentAiModelResult:
    """Ensure one configured model exists, is active, and is the role default."""

    org = platform_lens.get_or_create_platform_org()
    existing = _find_existing_model(org=org, config=config, role=role)
    if not _test_connection(config):
        return DeploymentAiModelResult(
            action="updated" if existing else "created",
            connectivity_ok=False,
            applied=False,
        )

    action: Literal["created", "updated"]
    if existing and existing.get("uuid"):
        config_uuid = uuid.UUID(str(existing["uuid"]))
        sl_client.request_json(
            "PUT",
            f"/api/v1/admin/llm-config/{config_uuid}/",
            json_body=_source_lens_payload(config),
        )
        action = "updated"
    else:
        created = sl_client.request_json(
            "POST",
            "/api/v1/admin/llm-config/",
            json_body=_source_lens_payload(config),
        )
        if not isinstance(created, dict) or not created.get("uuid"):
            raise sl_client.LensBridgeError(
                "SourceLens did not return the created AI model identifier."
            )
        config_uuid = uuid.UUID(str(created["uuid"]))
        action = "created"

    link = org_models.register_org_model(
        org=org,
        sl_config_uuid=config_uuid,
        ensure_agent_default=role == "agent",
    )
    org_models.set_model_display_name(link, config.display_name)
    defaults = provisioning.get_or_create_org_link(org)
    field_name = (
        "default_agent_model_ref" if role == "agent" else "default_multimodal_model_ref"
    )
    if getattr(defaults, field_name) != config_uuid:
        setattr(defaults, field_name, config_uuid)
        defaults.save(update_fields=[field_name, "updated_at"])
    return DeploymentAiModelResult(action=action, connectivity_ok=True)
