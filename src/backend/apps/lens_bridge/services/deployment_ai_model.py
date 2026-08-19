"""Idempotent deployment ownership for the platform's default AI model."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.db import DatabaseError, transaction

from apps.lens_bridge.models import LensOrgLink, LensOrgModelLink
from apps.lens_bridge.services import platform_lens, provisioning, sl_client

AiModelRole = Literal["agent", "multimodal"]

DEPLOYMENT_AGENT_MODEL_MANAGEMENT_KEY = "deployment-agent"
DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY = "deployment-multimodal"
LEGACY_DEPLOYMENT_MODEL_MANAGEMENT_KEY = "deployment-default"
# Backwards-compatible import for tests and upgrade tooling that still names
# the original single deployment-managed model.
DEPLOYMENT_MODEL_MANAGEMENT_KEY = DEPLOYMENT_AGENT_MODEL_MANAGEMENT_KEY
logger = logging.getLogger(__name__)


class DeploymentAiModelConfigurationError(ValueError):
    """Raised when deployment input is incomplete or unsafe."""


def _required_single_line(
    values: Mapping[str, Any],
    key: str,
    *,
    max_length: int,
) -> str:
    value = str(values.get(key) or "").strip()
    if not value:
        raise DeploymentAiModelConfigurationError(f"{key} is required")
    if len(value) > max_length or re.search(r"[\x00\r\n]", value):
        raise DeploymentAiModelConfigurationError(
            f"{key} must be a single-line value of at most {max_length} characters"
        )
    return value


def _validated_api_base(values: Mapping[str, Any]) -> str:
    value = _required_single_line(values, "api_base", max_length=2048)
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as exc:
        raise DeploymentAiModelConfigurationError(
            "api_base must be a valid HTTPS URL"
        ) from exc
    if (
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
    normalized_path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, normalized_path, "", ""))


def _coerced_bool(values: Mapping[str, Any], key: str) -> bool:
    """Coerce a JSON boolean or common string representation to bool."""
    value = values.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


@dataclass(frozen=True)
class DeploymentAiModelConfig:
    """Validated deployment input for one OpenAI-compatible model."""

    provider: str
    model_id: str
    display_name: str
    api_base: str
    api_key: str
    # SourceLens >= 0.39 validates multimodal models against an explicit
    # vision-capability declaration before it allows assistant creation.
    supports_vision: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> DeploymentAiModelConfig:
        """Validate and normalize a JSON-compatible configuration mapping."""

        provider = _required_single_line(values, "provider", max_length=64).lower()
        if not re.fullmatch(r"[a-z0-9_]+", provider):
            raise DeploymentAiModelConfigurationError(
                "provider must contain only lowercase letters, numbers, and underscores"
            )
        return cls(
            provider=provider,
            model_id=_required_single_line(values, "model_id", max_length=255),
            display_name=_required_single_line(
                values,
                "display_name",
                max_length=160,
            ),
            api_base=_validated_api_base(values),
            api_key=_required_single_line(values, "api_key", max_length=4096),
            supports_vision=_coerced_bool(values, "supports_vision"),
        )


@dataclass(frozen=True)
class DeploymentAiModelResult:
    """Sanitized result suitable for management-command output."""

    action: Literal["created", "updated", "recreated"]
    connectivity_ok: bool
    applied: bool = True


def _deployment_fingerprint(config: DeploymentAiModelConfig) -> str:
    """Return a keyed fingerprint without persisting deployment secrets."""

    raw = json.dumps(
        {
            "provider": config.provider,
            "model_id": config.model_id,
            "display_name": config.display_name,
            "api_base": config.api_base,
            "api_key": config.api_key,
            "supports_vision": config.supports_vision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        str(settings.SECRET_KEY).encode("utf-8"),
        raw,
        hashlib.sha256,
    ).hexdigest()


def _source_lens_payload(
    config: DeploymentAiModelConfig,
    *,
    make_default: bool | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": config.provider,
        "config": {
            "model": config.model_id,
            "api_base": config.api_base,
            "api_key": config.api_key,
            "supports_vision": config.supports_vision,
        },
        "is_active": True,
    }
    if make_default is not None:
        payload["is_default"] = make_default
    return payload


def _source_lens_model(config_uuid: uuid.UUID) -> dict[str, Any] | None:
    try:
        data = sl_client.request_json(
            "GET",
            f"/api/v1/admin/llm-config/{config_uuid}/",
        )
    except sl_client.LensBridgeError as exc:
        if exc.status_code == 404:
            return None
        raise
    if not isinstance(data, dict):
        raise sl_client.LensBridgeError("SourceLens returned an invalid AI model.")
    return data


def _source_lens_model_is_active(config_ref: str) -> bool:
    data = _source_lens_model(uuid.UUID(config_ref))
    if data is None or data.get("is_active") is False:
        return False
    status = str(data.get("status") or "").strip().lower()
    return status not in {"inactive", "disabled"}


def _sl_model_supports_vision(data: dict[str, Any] | None) -> bool:
    """Return whether the installed SourceLens model declares vision support."""
    if not isinstance(data, dict):
        return False
    config = data.get("config")
    if not isinstance(config, dict):
        return False
    value = config.get("supports_vision")
    return value is True or str(value).strip().lower() in {"true", "1", "yes", "on"}


def _created_uuid(data: Any) -> uuid.UUID:
    if not isinstance(data, dict) or not data.get("uuid"):
        raise sl_client.LensBridgeError(
            "SourceLens did not return the created AI model identifier."
        )
    try:
        return uuid.UUID(str(data["uuid"]))
    except (TypeError, ValueError) as exc:
        raise sl_client.LensBridgeError(
            "SourceLens returned an invalid AI model identifier."
        ) from exc


@transaction.atomic
def _persist_link(
    *,
    link: LensOrgModelLink | None,
    config_uuid: uuid.UUID,
    display_name: str,
    management_key: str,
    role: AiModelRole,
    preserve_existing: bool,
    deployment_fingerprint: str,
) -> LensOrgModelLink:
    """Persist the current link while retaining live historical identities."""

    org = platform_lens.get_or_create_platform_org()
    if link is None:
        return LensOrgModelLink.all_objects.create(
            organization=org,
            sl_config_uuid=config_uuid,
            display_name=display_name,
            management_key=management_key,
            deployment_role=role,
            deployment_fingerprint=deployment_fingerprint,
        )

    link = LensOrgModelLink.all_objects.select_for_update().get(pk=link.pk)
    if preserve_existing and link.sl_config_uuid != config_uuid:
        link.management_key = (
            f"deploy-{role}-history-{link.sl_config_uuid.hex}"
        )
        link.deployment_role = role
        link.is_deployment_history = True
        link.deployment_fingerprint = ""
        link.save(
            update_fields=[
                "management_key",
                "deployment_role",
                "is_deployment_history",
                "deployment_fingerprint",
                "updated_at",
            ]
        )
        return LensOrgModelLink.all_objects.create(
            organization=org,
            sl_config_uuid=config_uuid,
            display_name=display_name,
            management_key=management_key,
            deployment_role=role,
            deployment_fingerprint=deployment_fingerprint,
        )

    link.sl_config_uuid = config_uuid
    link.display_name = display_name
    link.management_key = management_key
    link.deployment_role = role
    link.is_deployment_history = False
    link.is_deleted = False
    link.deployment_fingerprint = deployment_fingerprint
    link.deleted_at = None
    link.save(
        update_fields=[
            "sl_config_uuid",
            "display_name",
            "management_key",
            "deployment_role",
            "is_deployment_history",
            "is_deleted",
            "deployment_fingerprint",
            "deleted_at",
            "updated_at",
        ]
    )
    return link


def _test_connection(config_uuid: uuid.UUID) -> bool:
    try:
        response = sl_client.request_json(
            "POST",
            "/api/v1/admin/llm-config/test-call/",
            json_body={
                "config_uuid": str(config_uuid),
                "prompt": "Respond with exactly OK and no explanation.",
                "max_tokens": 512,
            },
            timeout=90,
        )
    except sl_client.LensBridgeError:
        return False
    if isinstance(response, dict):
        if "ok" in response:
            return bool(response["ok"])
        if "success" in response:
            return bool(response["success"])
    return False


def _management_keys_for_role(role: AiModelRole) -> tuple[str, ...]:
    """Return current and adoptable deployment keys for one model role."""

    if role == "agent":
        return (
            DEPLOYMENT_AGENT_MODEL_MANAGEMENT_KEY,
            LEGACY_DEPLOYMENT_MODEL_MANAGEMENT_KEY,
        )
    return (DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY,)


def _management_key_for_role(role: AiModelRole) -> str:
    """Return the canonical deployment management key for one role."""

    if role == "agent":
        return DEPLOYMENT_AGENT_MODEL_MANAGEMENT_KEY
    return DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY


def _set_role_default(
    *,
    defaults_id: int,
    role: AiModelRole,
    config_uuid: uuid.UUID,
) -> None:
    """Set one HFL role default while holding its organization row lock."""

    defaults = LensOrgLink.objects.select_for_update().get(pk=defaults_id)
    field_name = (
        "default_agent_model_ref"
        if role == "agent"
        else "default_multimodal_model_ref"
    )
    if getattr(defaults, field_name) == config_uuid:
        return
    setattr(defaults, field_name, config_uuid)
    defaults.save(update_fields=[field_name, "updated_at"])


def ensure_platform_ai_model(
    config: DeploymentAiModelConfig,
    *,
    role: AiModelRole = "agent",
) -> DeploymentAiModelResult:
    """Create, update, or repair the deployment-owned SourceLens model.

    Candidates are tested before HFL promotes a role pointer. SourceLens's
    process-wide default is never changed, and an administrator's later HFL
    role selection is preserved across deployment-managed updates.
    """

    org = platform_lens.get_or_create_platform_org()
    platform_defaults = provisioning.get_or_create_org_link(org)
    management_key = _management_key_for_role(role)
    link = (
        LensOrgModelLink.all_objects.filter(
            organization=org,
            management_key__in=_management_keys_for_role(role),
        )
        .order_by("id")
        .first()
    )
    current = _source_lens_model(link.sl_config_uuid) if link is not None else None
    selected_model_ref = (
        platform_defaults.default_agent_model_ref
        if role == "agent"
        else platform_defaults.default_multimodal_model_ref
    )
    selected_ref = str(selected_model_ref) if selected_model_ref else ""
    managed_ref = str(link.sl_config_uuid) if link is not None else ""
    first_adoption = link is None
    deployment_fingerprint = _deployment_fingerprint(config)
    should_select_managed = first_adoption or not selected_ref or selected_ref == managed_ref
    if not should_select_managed:
        selected_owned = LensOrgModelLink.objects.filter(
            organization=org,
            sl_config_uuid=selected_model_ref,
            is_deleted=False,
            is_deployment_history=False,
        ).exists()
        if not selected_owned or not _source_lens_model_is_active(selected_ref):
            should_select_managed = True

    if (
        current is not None
        and link is not None
        and link.deployment_fingerprint == deployment_fingerprint
    ):
        connectivity_ok = _test_connection(link.sl_config_uuid)
        if connectivity_ok:
            # Upgrade repair: SourceLens >= 0.39 requires multimodal models to
            # declare vision capability before assistant creation. Patch the
            # installed model in place instead of recreating it, so the
            # config_uuid and any administrator selections stay stable.
            if config.supports_vision and not _sl_model_supports_vision(current):
                # Deliberately omit is_default from the patch payload: the PUT
                # endpoint applies partial updates and would otherwise clear
                # SourceLens's process-wide default if this model holds it.
                sl_client.request_json(
                    "PUT",
                    f"/api/v1/admin/llm-config/{link.sl_config_uuid}/",
                    json_body=_source_lens_payload(config),
                )
                logger.info(
                    "Repaired SourceLens multimodal model %s vision-capability "
                    "declaration.",
                    link.sl_config_uuid,
                )
            if should_select_managed:
                with transaction.atomic():
                    _set_role_default(
                        defaults_id=platform_defaults.id,
                        role=role,
                        config_uuid=link.sl_config_uuid,
                    )
            return DeploymentAiModelResult(
                action="updated",
                connectivity_ok=True,
            )
        logger.warning(
            "Deployment-managed %s model failed its recheck; "
            "validating a replacement candidate.",
            role,
        )

    created = sl_client.request_json(
        "POST",
        "/api/v1/admin/llm-config/",
        json_body=_source_lens_payload(config, make_default=False),
    )
    config_uuid = _created_uuid(created)
    action: Literal["created", "updated", "recreated"] = (
        "created" if first_adoption else "recreated"
    )

    connectivity_ok = _test_connection(config_uuid)
    if not connectivity_ok:
        try:
            sl_client.request_json(
                "DELETE",
                f"/api/v1/admin/llm-config/{config_uuid}/",
            )
        except sl_client.LensBridgeError:
            logger.warning(
                "Unable to remove rejected deployment-managed AI model."
            )
        return DeploymentAiModelResult(
            action=action,
            connectivity_ok=False,
            applied=False,
        )

    try:
        with transaction.atomic():
            _persist_link(
                link=link,
                config_uuid=config_uuid,
                display_name=config.display_name,
                management_key=management_key,
                role=role,
                preserve_existing=(current is not None),
                deployment_fingerprint=deployment_fingerprint,
            )
            if should_select_managed:
                _set_role_default(
                    defaults_id=platform_defaults.id,
                    role=role,
                    config_uuid=config_uuid,
                )
    except DatabaseError:
        if action in {"created", "recreated"}:
            try:
                sl_client.request_json(
                    "DELETE",
                    f"/api/v1/admin/llm-config/{config_uuid}/",
                )
            except sl_client.LensBridgeError:
                logger.warning(
                    "Unable to remove orphaned deployment-managed SourceLens model."
                )
        raise
    return DeploymentAiModelResult(
        action=action,
        connectivity_ok=connectivity_ok,
    )
