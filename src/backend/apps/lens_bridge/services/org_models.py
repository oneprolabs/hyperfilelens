"""Organization-scoped SourceLens LLM config ownership."""

from __future__ import annotations

import uuid
from typing import Any

from django.contrib.auth import get_user_model
from rest_framework.exceptions import NotFound, ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import LensOrgLink, LensOrgModelLink
from apps.lens_bridge.services import provisioning, sl_client

User = get_user_model()


def org_model_links(org: Organization):
    return LensOrgModelLink.objects.filter(
        organization=org,
        is_deleted=False,
    ).order_by("created_at", "id")


def default_model_display_name(data: dict[str, Any]) -> str:
    provider = str(data.get("provider") or "provider").strip()
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    model = str(config.get("model") or "—").strip() or "—"
    return f"{provider} · {model}"


def merge_model_display_name(
    data: dict[str, Any],
    link: LensOrgModelLink | None,
    *,
    defaults: LensOrgLink | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        return data
    out = dict(data)
    stored = (link.display_name if link else "") or ""
    out["name"] = stored.strip() or default_model_display_name(out)
    out["deployment_managed"] = bool(
        link and (link.management_key or link.deployment_role)
    )
    out["deployment_role"] = link.deployment_role if link else ""
    out["is_deployment_history"] = bool(
        link and link.is_deployment_history
    )
    if defaults is None and link is not None:
        defaults = provisioning.get_or_create_org_link(link.organization)
    out["is_default_agent"] = bool(
        link
        and defaults
        and defaults.default_agent_model_ref == link.sl_config_uuid
    )
    out["is_default_multimodal"] = bool(
        link
        and defaults
        and defaults.default_multimodal_model_ref == link.sl_config_uuid
    )
    return out


def deployment_managed_model_uuid(
    org: Organization,
    *,
    role: str = "agent",
) -> uuid.UUID | None:
    """Return the deployment-managed model UUID for one explicit role."""

    from apps.lens_bridge.services import deployment_ai_model

    management_keys = (
        [
            deployment_ai_model.DEPLOYMENT_AGENT_MODEL_MANAGEMENT_KEY,
            deployment_ai_model.LEGACY_DEPLOYMENT_MODEL_MANAGEMENT_KEY,
        ]
        if role == "agent"
        else [deployment_ai_model.DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY]
    )

    return (
        org_model_links(org)
        .filter(management_key__in=management_keys)
        .values_list("sl_config_uuid", flat=True)
        .first()
    )


def set_model_display_name(link: LensOrgModelLink, name: str | None) -> None:
    if name is None:
        return
    cleaned = str(name).strip()[:160]
    if cleaned == link.display_name:
        return
    link.display_name = cleaned
    link.save(update_fields=["display_name", "updated_at"])


def register_org_model(
    *,
    org: Organization,
    sl_config_uuid: uuid.UUID,
    created_by: User | None = None,
) -> LensOrgModelLink:
    link, created = LensOrgModelLink.objects.get_or_create(
        organization=org,
        sl_config_uuid=sl_config_uuid,
        defaults={"created_by": created_by},
    )
    if not created and link.is_deleted:
        link.is_deleted = False
        link.deleted_at = None
        if created_by is not None:
            link.created_by = created_by
        link.save(update_fields=["is_deleted", "deleted_at", "created_by", "updated_at"])
    ensure_org_default_model(org)
    return link


def require_org_model(org: Organization, config_uuid: uuid.UUID) -> LensOrgModelLink:
    link = org_model_links(org).filter(sl_config_uuid=config_uuid).first()
    if link is None:
        raise NotFound("AI model not found for this organization.")
    return link


def ensure_org_default_model(org: Organization) -> LensOrgLink:
    org_link = provisioning.get_or_create_org_link(org)
    from apps.lens_bridge.services import deployment_ai_model

    agent_links = org_model_links(org).filter(
        is_deployment_history=False,
    ).exclude(
        deployment_role=LensOrgModelLink.DeploymentRole.MULTIMODAL,
    ).exclude(
        management_key=(
            deployment_ai_model.DEPLOYMENT_MULTIMODAL_MODEL_MANAGEMENT_KEY
        )
    )
    current_ref = org_link.default_agent_model_ref
    if current_ref:
        if agent_links.filter(
            sl_config_uuid=current_ref
        ).exists():
            return org_link

    first_uuid = agent_links.values_list("sl_config_uuid", flat=True).first()
    if current_ref != first_uuid:
        org_link.default_agent_model_ref = first_uuid
        org_link.save(update_fields=["default_agent_model_ref", "updated_at"])
    return org_link


def ensure_org_model_defaults(org: Organization) -> LensOrgLink:
    """Repair explicit Agent and multimodal defaults without crossing roles."""

    org_link = ensure_org_default_model(org)
    multimodal_ref = org_link.default_multimodal_model_ref
    if multimodal_ref and not org_model_links(org).filter(
        sl_config_uuid=multimodal_ref,
        is_deployment_history=False,
    ).exists():
        org_link.default_multimodal_model_ref = None
        org_link.save(
            update_fields=["default_multimodal_model_ref", "updated_at"]
        )
    return org_link


def list_org_model_configs(org: Organization) -> list[dict[str, Any]]:
    links = list(org_model_links(org))
    if not links:
        ensure_org_default_model(org)
        return []

    from apps.lens_bridge.services.assistants import _unwrap_list

    defaults = ensure_org_model_defaults(org)
    source_lens_rows: dict[uuid.UUID, dict[str, Any]] = {}
    list_available = True
    try:
        raw = sl_client.request_json("GET", "/api/v1/admin/llm-config/")
        for row in _unwrap_list(raw):
            if not isinstance(row, dict) or not row.get("uuid"):
                continue
            try:
                source_lens_rows[uuid.UUID(str(row["uuid"]))] = row
            except (TypeError, ValueError):
                continue
    except sl_client.LensBridgeError:
        list_available = False

    rows: list[dict[str, Any]] = []
    for link in links:
        data = source_lens_rows.get(link.sl_config_uuid)
        if data is None and not list_available:
            try:
                data = sl_client.request_json(
                    "GET",
                    f"/api/v1/admin/llm-config/{link.sl_config_uuid}/",
                )
            except sl_client.LensBridgeError:
                continue
        if isinstance(data, dict):
            rows.append(
                merge_model_display_name(
                    data,
                    link,
                    defaults=defaults,
                )
            )

    return rows


def list_all_llm_configs(*, org: Organization | None = None) -> list[dict[str, Any]]:
    """Full SL admin LLM config list (no org-link filter).

    Optionally merges display_name from HFL links for ``org`` when present.
    """
    from apps.lens_bridge.services.assistants import _unwrap_list

    try:
        raw = sl_client.request_json("GET", "/api/v1/admin/llm-config/")
        rows = _unwrap_list(raw)
    except sl_client.LensBridgeError:
        # Fallback: some SL builds only expose per-uuid GET.
        if org is not None:
            return list_org_model_configs(org)
        return []

    if org is None:
        return [
            merge_model_display_name(row, None) if isinstance(row, dict) else row
            for row in rows
            if isinstance(row, dict)
        ]

    links = {
        link.sl_config_uuid: link
        for link in org_model_links(org)
    }
    defaults = provisioning.get_or_create_org_link(org)
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw_uuid = row.get("uuid")
        link = None
        if raw_uuid:
            try:
                link = links.get(uuid.UUID(str(raw_uuid)))
            except (TypeError, ValueError):
                link = None
        out.append(
            merge_model_display_name(
                row,
                link,
                defaults=defaults,
            )
        )
    return out


def active_llm_configs(*, org: Organization) -> list[dict[str, Any]]:
    """Return active SourceLens models linked to exactly one organization."""
    rows: list[dict[str, Any]] = []
    for row in list_org_model_configs(org):
        status = str(row.get("status") or "").strip().lower()
        if row.get("is_active") is False or status in {"inactive", "disabled"}:
            continue
        if row.get("uuid"):
            rows.append(row)
    return rows


def active_llm_configs_available_to_org(
    org: Organization,
) -> list[dict[str, Any]]:
    """Return active models owned by the org or inherited from the platform.

    Platform-managed defaults are intentionally available to every tenant, while
    tenant-owned models remain isolated to their owning organization.  Keep the
    tenant row first so an organization-specific display name wins if the same
    SourceLens configuration is represented in both scopes.
    """

    from apps.lens_bridge.services import platform_lens

    rows = active_llm_configs(org=org)
    platform_org = platform_lens.get_or_create_platform_org()
    if org.pk == platform_org.pk:
        return rows

    seen = {str(row.get("uuid")) for row in rows if row.get("uuid")}
    for row in active_llm_configs(org=platform_org):
        uuid_value = str(row.get("uuid") or "")
        if uuid_value and uuid_value not in seen:
            rows.append(row)
            seen.add(uuid_value)
    return rows


def delete_org_model(org: Organization, config_uuid: uuid.UUID) -> None:
    require_org_model(org, config_uuid)
    sl_client.request_json("DELETE", f"/api/v1/admin/llm-config/{config_uuid}/")
    LensOrgModelLink.objects.filter(
        organization=org,
        sl_config_uuid=config_uuid,
    ).update(is_deleted=True)
    org_link = provisioning.get_or_create_org_link(org)
    if org_link.default_agent_model_ref == config_uuid:
        org_link.default_agent_model_ref = None
    if org_link.default_multimodal_model_ref == config_uuid:
        org_link.default_multimodal_model_ref = None
    org_link.save(
        update_fields=[
            "default_agent_model_ref",
            "default_multimodal_model_ref",
            "updated_at",
        ]
    )
    ensure_org_default_model(org)


def validate_default_model_ref(
    org: Organization,
    config_uuid: uuid.UUID | None,
    *,
    field_name: str = "default_agent_model_ref",
) -> None:
    if config_uuid is None:
        return
    if not org_model_links(org).filter(
        sl_config_uuid=config_uuid,
        is_deployment_history=False,
    ).exists():
        raise ValidationError(
            {field_name: "Model does not belong to this organization."}
        )


def validate_agent_model_ref(
    org: Organization,
    config_uuid: uuid.UUID | None,
    *,
    field_name: str = "agent_model_ref",
) -> None:
    """Validate a user-selected model without allowing the multimodal role."""

    if config_uuid is None:
        return
    link = org_model_links(org).filter(
        sl_config_uuid=config_uuid,
        is_deployment_history=False,
    ).first()
    if link is None:
        from apps.lens_bridge.services import platform_lens

        platform_org = platform_lens.get_or_create_platform_org()
        if org.pk != platform_org.pk:
            link = org_model_links(platform_org).filter(
                sl_config_uuid=config_uuid,
                is_deployment_history=False,
            ).first()
    if link is None:
        raise ValidationError(
            {field_name: "Model is not available to this organization."}
        )
    if link.deployment_role == LensOrgModelLink.DeploymentRole.MULTIMODAL:
        raise ValidationError(
            {field_name: "Select an Agent model for Chat responses."}
        )
    from apps.lens_bridge.services import provisioning

    _, multimodal_ref = provisioning.configured_default_model_refs_for_org(org)
    if multimodal_ref and config_uuid == uuid.UUID(str(multimodal_ref)):
        raise ValidationError(
            {field_name: "Select an Agent model for Chat responses."}
        )
