"""SourceLens assistant helpers for Insights Engine."""

from __future__ import annotations

import uuid as uuid_lib
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from rest_framework.exceptions import NotFound, ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensAssistantLink,
    LensGatewayLink,
    LensKnowledgeSource,
    LensSessionLink,
)
from apps.lens_bridge.services import assistant_access, sl_client
from apps.lens_bridge.services.provisioning import _slugify_assistant, get_or_create_org_link


def _extract_binding_uuids(payload: dict[str, Any], binding_key: str, uuid_key: str) -> list[uuid_lib.UUID]:
    rows = payload.get(binding_key) or []
    if not isinstance(rows, list):
        return []
    uuids: list[uuid_lib.UUID] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = row.get(uuid_key)
        if raw in (None, ""):
            continue
        uuids.append(uuid_lib.UUID(str(raw)))
    return uuids


def _validate_assistant_tool_bindings(org: Organization, payload: dict[str, Any]) -> None:
    from apps.lens_bridge.services import org_mcp_servers, org_skills

    org_skills.validate_org_skill_uuids(
        org,
        _extract_binding_uuids(payload, "skill_bindings", "skill_uuid"),
    )
    org_mcp_servers.validate_org_mcp_uuids(
        org,
        _extract_binding_uuids(payload, "mcp_bindings", "mcp_uuid"),
    )


def _unwrap_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    if isinstance(raw, dict):
        results = raw.get("results")
        if isinstance(results, list):
            return [row for row in results if isinstance(row, dict)]
    return []


def _org_prefix(org: Organization) -> str:
    return get_or_create_org_link(org).resolved_prefix()


def _belongs_to_org(org: Organization, assistant: dict[str, Any]) -> bool:
    slug = str(assistant.get("slug") or "")
    return slug.startswith(f"{_org_prefix(org)}-")


def _ks_by_assistant_uuid(org: Organization) -> dict[str, LensKnowledgeSource]:
    return {
        str(ks.sl_assistant_uuid): ks
        for ks in LensKnowledgeSource.objects.filter(
            organization=org,
            sl_assistant_uuid__isnull=False,
        ).select_related("gateway")
    }


def _serialize_list_row(
    org: Organization,
    item: dict[str, Any],
    *,
    ks_by_uuid: dict[str, LensKnowledgeSource],
    link_by_uuid: dict[str, LensAssistantLink],
) -> dict[str, Any] | None:
    uuid_str = str(item.get("uuid") or "")
    ks = ks_by_uuid.get(uuid_str)
    link = link_by_uuid.get(uuid_str)
    if link is None and uuid_str:
        return None
    if ks is None and link is not None and link.knowledge_source_id is not None:
        ks = link.knowledge_source
    selected_dirs = item.get("selected_dirs") or []
    first_dir = ""
    if isinstance(selected_dirs, list) and selected_dirs:
        first = selected_dirs[0]
        if isinstance(first, dict):
            first_dir = str(first.get("path") or "")
    row = {
        "uuid": uuid_str,
        "name": item.get("name") or item.get("slug") or "",
        "slug": item.get("slug") or "",
        "status": item.get("status") or "unknown",
        "lensnode_uuid": item.get("lensnode") or item.get("lensnode_uuid"),
        "selected_task": item.get("selected_task") or "",
        "selected_dir": first_dir,
        "agent_model_ref": item.get("agent_model_ref"),
        "multimodal_model_ref": item.get("multimodal_model_ref"),
        "supports_document_attachments": bool(
            item.get("supports_document_attachments")
        ),
        "agent_rounds": item.get("agent_rounds") or "",
        "knowledge_source_id": ks.id if ks else None,
        "knowledge_source_name": ks.name if ks else None,
        "knowledge_source_status": ks.status if ks else None,
        "gateway_name": ks.gateway.name if ks and ks.gateway_id else None,
    }
    return assistant_access.merge_link_fields(row, link)


def _strip_hfl_visibility(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None, int | None]:
    body = dict(payload)
    visibility_scope = body.pop("visibility_scope", None)
    body.pop("visibility", None)
    ks_id = body.pop("knowledge_source_id", None)
    body["visibility"] = "private"
    return body, visibility_scope, ks_id


def _resolve_visibility_scope(raw: Any, *, default: str) -> str:
    try:
        return assistant_access.parse_visibility_scope(raw, default=default)
    except ValueError as exc:
        raise ValidationError({"visibility_scope": str(exc)}) from exc


def _upsert_assistant_link(
    *,
    org: Organization,
    assistant_uuid: uuid_lib.UUID,
    visibility_scope: str,
    user: AbstractBaseUser | None,
    knowledge_source_id: int | None,
) -> LensAssistantLink:
    ks = None
    if knowledge_source_id not in (None, ""):
        ks = LensKnowledgeSource.objects.filter(organization=org, pk=knowledge_source_id).first()
    owner_user = user if visibility_scope == LensAssistantLink.VisibilityScope.USER else None
    link = assistant_access.get_assistant_link(org, assistant_uuid)
    if link is None:
        link = assistant_access.ensure_assistant_link(
            org=org,
            sl_assistant_uuid=assistant_uuid,
            knowledge_source=ks,
            visibility_scope=visibility_scope,
            owner_user=owner_user,
            created_by=user,
        )
        if ks is not None and ks.sl_assistant_uuid != assistant_uuid:
            ks.sl_assistant_uuid = assistant_uuid
            ks.save(update_fields=["sl_assistant_uuid", "updated_at"])
        return link
    link.visibility_scope = visibility_scope
    link.owner_user = owner_user
    if ks is not None:
        link.knowledge_source = ks
    update_fields = ["visibility_scope", "owner_user", "updated_at"]
    if ks is not None:
        update_fields.append("knowledge_source")
    link.save(update_fields=update_fields)
    if ks is not None and ks.sl_assistant_uuid != assistant_uuid:
        ks.sl_assistant_uuid = assistant_uuid
        ks.save(update_fields=["sl_assistant_uuid", "updated_at"])
    return link


def list_org_assistants(
    org: Organization,
    *,
    user: AbstractBaseUser,
    can_manage_all: bool = False,
) -> list[dict[str, Any]]:
    raw = sl_client.request_json("GET", "/api/lens/assistants/")
    items = _unwrap_list(raw)

    ks_by_uuid = _ks_by_assistant_uuid(org)
    link_by_uuid = assistant_access.links_for_org(org)

    rows: list[dict[str, Any]] = []
    for item in items:
        if not _belongs_to_org(org, item):
            continue
        uuid_str = str(item.get("uuid") or "")
        link = link_by_uuid.get(uuid_str)
        if link is None:
            continue
        row = _serialize_list_row(
            org,
            item,
            ks_by_uuid=ks_by_uuid,
            link_by_uuid=link_by_uuid,
        )
        if row is None:
            continue
        if not assistant_access.assistant_visible_to(
            user=user,
            link=link,
            can_manage_all=can_manage_all,
        ):
            continue
        rows.append(row)

    rows.sort(key=lambda row: (row.get("name") or "").lower())
    return rows


def get_org_assistant(
    org: Organization,
    assistant_uuid: uuid_lib.UUID,
    *,
    user: AbstractBaseUser | None = None,
    can_manage_all: bool = False,
) -> dict[str, Any]:
    data = sl_client.request_json("GET", f"/api/lens/assistants/{assistant_uuid}/")
    if not isinstance(data, dict) or not _belongs_to_org(org, data):
        raise NotFound("Assistant not found.")
    ks_by_uuid = _ks_by_assistant_uuid(org)
    link = assistant_access.get_assistant_link(org, assistant_uuid)
    if link is None:
        raise NotFound("Assistant not found.")
    if user is not None and not assistant_access.assistant_visible_to(
        user=user,
        link=link,
        can_manage_all=can_manage_all,
    ):
        raise NotFound("Assistant not found.")
    merged = assistant_access.merge_link_fields(dict(data), link)
    ks = ks_by_uuid.get(str(assistant_uuid))
    if ks is None and link.knowledge_source_id is not None:
        ks = link.knowledge_source
    if ks is not None:
        merged["knowledge_source_id"] = ks.id
        merged["knowledge_source_status"] = ks.status
    return merged


def _apply_retrieval_include_paths(
    selected_dirs: list[dict[str, Any]],
    include_paths: list[str] | None,
) -> list[dict[str, Any]]:
    if include_paths is None:
        return selected_dirs
    cleaned = [str(path).strip() for path in include_paths if str(path).strip()]
    result: list[dict[str, Any]] = []
    for item in selected_dirs:
        if not isinstance(item, dict):
            continue
        entry = {key: value for key, value in item.items() if key != "retrieval_scope"}
        if cleaned:
            entry["retrieval_scope"] = {"include_paths": cleaned}
        result.append(entry)
    return result


def _knowledge_source_execution(org: Organization, ks: LensKnowledgeSource) -> dict[str, Any]:
    from apps.lens_bridge.services.gateway_execution import context_for_knowledge_source

    link = context_for_knowledge_source(
        tenant_organization=org,
        knowledge_source=ks,
    ).gateway_link
    lensnode_uuid = ks.sl_lensnode_uuid or (link.sl_lensnode_uuid if link else None)
    if not lensnode_uuid:
        raise ValidationError(
            {"knowledge_source_id": "Knowledge source gateway is not linked to a LensNode."}
        )
    from apps.lens_bridge.services.knowledge_source_sync import indexed_dir_paths

    try:
        indexed_paths = [path for path in indexed_dir_paths(ks) if path]
    except Exception as exc:
        raise ValidationError({"knowledge_source_id": str(exc)}) from exc
    if not indexed_paths:
        raise ValidationError(
            {"knowledge_source_id": "Knowledge source has no indexed paths yet. Wait for sync to finish."}
        )
    return {
        "lensnode_uuid": str(lensnode_uuid),
        "selected_dirs": [{"path": path} for path in indexed_paths],
    }


_TENANT_EXECUTION_FIELDS = frozenset({"lensnode", "lensnode_uuid", "selected_dirs"})


def _apply_knowledge_source_payload(
    org: Organization,
    payload: dict[str, Any],
    *,
    user: AbstractBaseUser | None = None,
    platform_passthrough: bool = False,
    require_knowledge_source: bool = False,
) -> dict[str, Any]:
    """Derive tenant Assistant execution fields from an authorized KS."""

    ks_id = payload.pop("knowledge_source_id", None)
    include_paths = payload.pop("retrieval_include_paths", None)
    if include_paths is not None and not isinstance(include_paths, list):
        raise ValidationError({"retrieval_include_paths": "Must be a list of path rules."})

    if not platform_passthrough:
        supplied_execution_fields = sorted(_TENANT_EXECUTION_FIELDS.intersection(payload))
        if supplied_execution_fields:
            raise ValidationError(
                {
                    "knowledge_source_id": (
                        "Tenant Assistants must derive LensNode and directory settings "
                        "from a knowledge source."
                    )
                }
            )

    if ks_id in (None, ""):
        if require_knowledge_source and not platform_passthrough:
            raise ValidationError(
                {"knowledge_source_id": "Knowledge source is required."}
            )
        if include_paths is not None and payload.get("selected_dirs"):
            payload["selected_dirs"] = _apply_retrieval_include_paths(
                payload["selected_dirs"],
                include_paths,
            )
        return payload

    ks = (
        LensKnowledgeSource.objects.filter(organization=org, pk=ks_id)
        .select_related("gateway", "gateway_link", "gateway_link__gateway", "gateway_link__organization")
        .first()
    )
    if ks is None:
        raise ValidationError({"knowledge_source_id": "Knowledge source not found."})
    if not platform_passthrough:
        if user is None:
            raise ValidationError(
                {"knowledge_source_id": "Knowledge source owner is required."}
            )
        if ks.created_by_id != user.id:
            raise ValidationError(
                {
                    "knowledge_source_id": (
                        "Knowledge source is not owned by the current user."
                    )
                }
            )
        gateway_link = ks.gateway_link
        if (
            gateway_link.scope == LensGatewayLink.GatewayScope.USER
            and gateway_link.owner_user_id != user.id
        ):
            raise ValidationError(
                {
                    "knowledge_source_id": (
                        "Knowledge source data gateway is not owned by the current user."
                    )
                }
            )
    if ks.session_links.exclude(
        lifecycle_status=LensSessionLink.LifecycleStatus.DELETED
    ).exists():
        raise ValidationError(
            {
                "knowledge_source_id": (
                    "Chat-managed knowledge sources cannot be rebound through "
                    "the Assistant API."
                )
            }
        )
    execution = _knowledge_source_execution(org, ks)
    payload["lensnode_uuid"] = execution["lensnode_uuid"]
    payload["selected_dirs"] = _apply_retrieval_include_paths(
        execution["selected_dirs"],
        include_paths,
    )
    return payload


def _serialize_knowledge_source_option(org: Organization, ks: LensKnowledgeSource) -> dict[str, Any]:
    from apps.lens_bridge.services.knowledge_source_sync import indexed_dir_paths, scope_entries

    link = ks.gateway_link
    lensnode_uuid = ks.sl_lensnode_uuid or (link.sl_lensnode_uuid if link else None)
    scope_paths = [
        str(item.get("source_path") or "").strip()
        for item in scope_entries(ks)
        if str(item.get("source_path") or "").strip()
    ]
    indexed_paths: list[str] = []
    try:
        indexed_paths = [path for path in indexed_dir_paths(ks) if path]
    except Exception:
        indexed_paths = []
    return {
        "id": ks.id,
        "name": ks.name,
        "gateway_name": ks.gateway.name,
        "status": ks.status,
        "lensnode_uuid": str(lensnode_uuid) if lensnode_uuid else "",
        "workspace_path_on_lensnode": ks.workspace_path_on_lensnode or "",
        "scope_paths": scope_paths,
        "indexed_paths": indexed_paths,
        "sl_assistant_uuid": str(ks.sl_assistant_uuid) if ks.sl_assistant_uuid else None,
    }


def create_org_assistant(
    org: Organization,
    body: dict[str, Any],
    *,
    user: AbstractBaseUser | None = None,
    platform_passthrough: bool = False,
) -> dict[str, Any]:
    payload, visibility_scope_raw, ks_id = _strip_hfl_visibility(body)
    visibility_scope = _resolve_visibility_scope(
        visibility_scope_raw,
        default=LensAssistantLink.VisibilityScope.USER,
    )
    if ks_id not in (None, ""):
        payload["knowledge_source_id"] = ks_id
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValidationError({"name": "Name is required."})
    slug = str(payload.get("slug") or "").strip()
    payload["slug"] = slug or _slugify_assistant(name, org)
    payload = _apply_knowledge_source_payload(
        org,
        payload,
        user=user,
        platform_passthrough=platform_passthrough,
        require_knowledge_source=True,
    )
    if not payload.get("lensnode_uuid"):
        raise ValidationError({"lensnode_uuid": "LensNode is required."})
    if not payload.get("selected_task"):
        raise ValidationError({"selected_task": "Task is required."})
    if not payload.get("selected_dirs"):
        raise ValidationError({"selected_dirs": "At least one directory is required."})
    _validate_assistant_tool_bindings(org, payload)
    data = sl_client.request_json("POST", "/api/lens/assistants/", json_body=payload)
    if not isinstance(data, dict):
        raise sl_client.LensBridgeError("SourceLens assistant create returned invalid payload.")
    assistant_uuid = uuid_lib.UUID(str(data["uuid"]))
    link = _upsert_assistant_link(
        org=org,
        assistant_uuid=assistant_uuid,
        visibility_scope=visibility_scope,
        user=user,
        knowledge_source_id=ks_id if ks_id not in (None, "") else None,
    )
    from apps.lens_bridge.services import org_skills

    org_skills.sync_assistant_skill_links(
        org=org,
        assistant_uuid=assistant_uuid,
        created_by=user,
    )
    return assistant_access.merge_link_fields(data, link)


def update_org_assistant(
    org: Organization,
    assistant_uuid: uuid_lib.UUID,
    body: dict[str, Any],
    *,
    user: AbstractBaseUser | None = None,
    can_manage_all: bool = False,
    platform_passthrough: bool = False,
) -> dict[str, Any]:
    if user is None and not can_manage_all:
        raise ValidationError(
            {"assistant": "Assistant management authorization is required."}
        )
    get_org_assistant(
        org,
        assistant_uuid,
        user=user,
        can_manage_all=can_manage_all,
    )
    _require_manual_assistant_management(org, assistant_uuid)
    payload, visibility_scope_raw, ks_id = _strip_hfl_visibility(body)
    if ks_id not in (None, ""):
        payload["knowledge_source_id"] = ks_id
    payload = _apply_knowledge_source_payload(
        org,
        payload,
        user=user,
        platform_passthrough=platform_passthrough,
    )
    _validate_assistant_tool_bindings(org, payload)
    data = sl_client.request_json(
        "PATCH",
        f"/api/lens/assistants/{assistant_uuid}/",
        json_body=payload,
    )
    if not isinstance(data, dict):
        raise sl_client.LensBridgeError("SourceLens assistant update returned invalid payload.")
    from apps.lens_bridge.services import org_skills

    org_skills.sync_assistant_skill_links(
        org=org,
        assistant_uuid=assistant_uuid,
        created_by=user,
    )
    link = assistant_access.get_assistant_link(org, assistant_uuid)
    if visibility_scope_raw is not None or ks_id not in (None, ""):
        visibility_scope = _resolve_visibility_scope(
            visibility_scope_raw,
            default=(
                link.visibility_scope
                if link is not None
                else LensAssistantLink.VisibilityScope.USER
            ),
        )
        link = _upsert_assistant_link(
            org=org,
            assistant_uuid=assistant_uuid,
            visibility_scope=visibility_scope,
            user=user,
            knowledge_source_id=ks_id if ks_id not in (None, "") else (link.knowledge_source_id if link else None),
        )
    elif link is None:
        ks = None
        if ks_id not in (None, ""):
            ks = LensKnowledgeSource.objects.filter(organization=org, pk=ks_id).first()
        link = assistant_access.ensure_assistant_link(
            org=org,
            sl_assistant_uuid=assistant_uuid,
            knowledge_source=ks,
            created_by=user,
        )
    return assistant_access.merge_link_fields(data, link)


def delete_org_assistant(
    org: Organization,
    assistant_uuid: uuid_lib.UUID,
    *,
    user: AbstractBaseUser | None = None,
    can_manage_all: bool = False,
) -> None:
    if user is None and not can_manage_all:
        raise ValidationError(
            {"assistant": "Assistant management authorization is required."}
        )
    get_org_assistant(
        org,
        assistant_uuid,
        user=user,
        can_manage_all=can_manage_all,
    )
    _require_manual_assistant_management(org, assistant_uuid)
    _delete_sl_assistant(assistant_uuid)
    assistant_access.soft_delete_assistant_link(org, assistant_uuid)
    affected_ks_ids = list(
        LensKnowledgeSource.objects.filter(
            organization=org,
            sl_assistant_uuid=assistant_uuid,
        ).values_list("id", flat=True)
    )
    LensKnowledgeSource.objects.filter(
        organization=org,
        sl_assistant_uuid=assistant_uuid,
    ).update(sl_assistant_uuid=None)
    for ks_id in affected_ks_ids:
        _reassign_ks_primary_assistant(org, ks_id)


def _require_manual_assistant_management(
    org: Organization,
    assistant_uuid: uuid_lib.UUID,
) -> LensAssistantLink:
    """Reject mutations owned by the durable Chat lifecycle."""

    link = assistant_access.get_assistant_link(org, assistant_uuid)
    if link is None:
        raise NotFound("Assistant not found.")
    has_live_chat = LensSessionLink.objects.filter(
        organization=org,
        sl_assistant_uuid=assistant_uuid,
    ).exclude(
        lifecycle_status=LensSessionLink.LifecycleStatus.DELETED
    ).exists()
    if (
        link.lifecycle_owner == LensAssistantLink.LifecycleOwner.CHAT
        or has_live_chat
    ):
        raise ValidationError(
            {
                "assistant": (
                    "Chat-managed assistants must be changed by deleting and "
                    "recreating the owning Chat."
                )
            }
        )
    return link


def _delete_sl_assistant(assistant_uuid: uuid_lib.UUID) -> None:
    """Retire an Assistant through SourceLens' supported archive contract.

    SourceLens versions 0.20.0 and later remove DELETE from Assistant CRUD and
    expose a POST archive action instead. HFL treats an already archived or
    missing 404 as idempotent success but preserves every other error for
    durable retry.
    """

    try:
        sl_client.request_json(
            "POST",
            f"/api/lens/assistants/{assistant_uuid}/archive/",
        )
    except sl_client.LensBridgeError as exc:
        status = getattr(exc, "status_code", None)
        if status == 404 or "404" in str(exc):
            return
        raise


def _reassign_ks_primary_assistant(org: Organization, ks_id: int) -> None:
    """Point KS.sl_assistant_uuid at another active assistant linked to the same KS."""
    from apps.lens_bridge.models import LensAssistantLink

    replacement = (
        LensAssistantLink.objects.filter(
            organization=org,
            knowledge_source_id=ks_id,
            is_deleted=False,
        )
        .order_by("-updated_at")
        .first()
    )
    if replacement is None:
        return
    LensKnowledgeSource.objects.filter(organization=org, pk=ks_id).update(
        sl_assistant_uuid=replacement.sl_assistant_uuid,
    )


def list_org_lensnodes(
    org: Organization,
    *,
    owner_user: AbstractBaseUser | None = None,
) -> list[dict[str, Any]]:
    links = LensGatewayLink.objects.filter(
        organization=org,
        sl_lensnode_uuid__isnull=False,
    )
    if owner_user is not None:
        links = links.filter(
            scope=LensGatewayLink.GatewayScope.USER,
            owner_user=owner_user,
        )
    linked = {
        str(link.sl_lensnode_uuid)
        for link in links
    }
    if not linked:
        return []
    raw = sl_client.request_json("GET", "/api/lens/admin/lensnodes/")
    items = _unwrap_list(raw)
    return [item for item in items if str(item.get("uuid") or "") in linked]


def assistant_form_options(
    org: Organization,
    *,
    user: AbstractBaseUser | None = None,
    platform_passthrough: bool = False,
) -> dict[str, Any]:
    from apps.lens_bridge.services import provisioning

    gateway_rows = []
    if platform_passthrough:
        from apps.lens_bridge.services import platform_lens

        links = platform_lens.platform_gateway_links().filter(sl_lensnode_uuid__isnull=False)
    else:
        if user is None:
            raise ValidationError(
                {"assistant": "Assistant form option owner is required."}
            )
        links = LensGatewayLink.objects.filter(
            organization=org,
            sl_lensnode_uuid__isnull=False,
            scope=LensGatewayLink.GatewayScope.USER,
            owner_user=user,
        ).select_related("gateway")

    for link in links:
        node = link.gateway
        snap = provisioning.sl_lensnode_snapshot_from_link(link)
        gateway_rows.append(
            {
                "gateway_id": node.id,
                "gateway_name": node.name,
                "lensnode_uuid": str(link.sl_lensnode_uuid),
                "workspace_root": snap.get("sl_workspace_path") or link.resolved_workspace_root(),
                "tasks": snap.get("sl_tasks") or [],
            }
        )

    skills: list[dict[str, Any]] = []
    mcps: list[dict[str, Any]] = []
    try:
        if platform_passthrough:
            from apps.lens_bridge.services import skills as skills_service

            skills = skills_service.list_skills()
        else:
            from apps.lens_bridge.services import org_skills

            skills = org_skills.list_org_skills(org)
    except sl_client.LensBridgeError:
        skills = []
    try:
        if platform_passthrough:
            from apps.lens_bridge.services import mcp_servers as mcp_servers_service

            mcps = mcp_servers_service.list_mcp_servers()
        else:
            from apps.lens_bridge.services import org_mcp_servers

            mcps = org_mcp_servers.list_org_mcp_servers(org)
    except sl_client.LensBridgeError:
        mcps = []

    ks_org = org
    ks_qs = LensKnowledgeSource.objects.filter(
        organization=org,
        created_by=user,
    )
    if platform_passthrough:
        from apps.lens_bridge.services import platform_lens as pl

        platform_org = pl.get_or_create_platform_org()
        ks_org = platform_org
        ks_qs = LensKnowledgeSource.objects.filter(organization=platform_org)

    return {
        "lensnodes": list_org_lensnodes(
            ks_org,
            owner_user=None if platform_passthrough else user,
        ),
        "gateways": gateway_rows,
        "knowledge_sources": [
            _serialize_knowledge_source_option(ks_org, ks)
            for ks in ks_qs.select_related("gateway").order_by("name", "id")
        ],
        "skills": [
            {
                "uuid": str(row.get("uuid") or ""),
                "name": row.get("name") or "",
                "slug": row.get("slug") or "",
                "enabled": row.get("enabled", True),
            }
            for row in skills
            if row.get("uuid")
        ],
        "mcps": [
            {
                "uuid": str(row.get("uuid") or ""),
                "name": row.get("name") or "",
                "transport": row.get("transport") or "",
                "endpoint": row.get("endpoint") or "",
                "enabled": row.get("enabled", True),
            }
            for row in mcps
            if row.get("uuid")
        ],
    }
