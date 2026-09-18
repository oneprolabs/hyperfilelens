"""Organization-scoped SourceLens MCP server ownership."""

from __future__ import annotations

import uuid as uuid_lib
from typing import Any

from django.contrib.auth import get_user_model
from rest_framework.exceptions import NotFound, ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import LensOrgMcpLink
from apps.lens_bridge.services import sl_client
from apps.lens_bridge.services.assistants import _unwrap_list
from apps.lens_bridge.services import mcp_servers as sl_mcp_servers

User = get_user_model()


def org_mcp_links(org: Organization):
    return LensOrgMcpLink.objects.filter(
        organization=org,
        is_deleted=False,
    ).order_by("created_at", "id")


def org_mcp_uuids(org: Organization) -> list[uuid_lib.UUID]:
    return list(org_mcp_links(org).values_list("sl_mcp_uuid", flat=True))


def register_org_mcp(
    *,
    org: Organization,
    sl_mcp_uuid: uuid_lib.UUID,
    created_by: User | None = None,
) -> LensOrgMcpLink:
    link, created = LensOrgMcpLink.objects.get_or_create(
        organization=org,
        sl_mcp_uuid=sl_mcp_uuid,
        defaults={"created_by": created_by},
    )
    if not created and link.is_deleted:
        link.is_deleted = False
        link.deleted_at = None
        if created_by is not None:
            link.created_by = created_by
        link.save(update_fields=["is_deleted", "deleted_at", "created_by", "updated_at"])
    return link


def require_org_mcp(org: Organization, mcp_uuid: uuid_lib.UUID) -> LensOrgMcpLink:
    link = org_mcp_links(org).filter(sl_mcp_uuid=mcp_uuid).first()
    if link is None:
        raise NotFound("MCP server not found for this organization.")
    return link


def list_org_mcp_servers(org: Organization) -> list[dict[str, Any]]:
    linked = {str(value) for value in org_mcp_uuids(org)}
    if not linked:
        return []
    raw = sl_client.request_json("GET", "/api/lens/admin/mcp-servers/")
    items = _unwrap_list(raw)
    rows = [item for item in items if str(item.get("uuid") or "") in linked]
    rows.sort(key=lambda row: (row.get("name") or "").lower())
    return rows


def get_org_mcp_server(org: Organization, mcp_uuid: uuid_lib.UUID) -> dict[str, Any]:
    require_org_mcp(org, mcp_uuid)
    return sl_mcp_servers.get_mcp_server(mcp_uuid)


def create_org_mcp_server(
    org: Organization,
    body: dict[str, Any],
    *,
    created_by: User | None = None,
) -> dict[str, Any]:
    data = sl_mcp_servers.create_mcp_server(body)
    mcp_uuid = data.get("uuid")
    if not mcp_uuid:
        raise sl_client.LensBridgeError("SourceLens MCP server create returned no uuid.")
    register_org_mcp(
        org=org,
        sl_mcp_uuid=uuid_lib.UUID(str(mcp_uuid)),
        created_by=created_by,
    )
    return data


def update_org_mcp_server(
    org: Organization,
    mcp_uuid: uuid_lib.UUID,
    body: dict[str, Any],
) -> dict[str, Any]:
    require_org_mcp(org, mcp_uuid)
    return sl_mcp_servers.update_mcp_server(mcp_uuid, body)


def delete_org_mcp_server(org: Organization, mcp_uuid: uuid_lib.UUID) -> None:
    require_org_mcp(org, mcp_uuid)
    sl_mcp_servers.delete_mcp_server(mcp_uuid)
    LensOrgMcpLink.objects.filter(
        organization=org,
        sl_mcp_uuid=mcp_uuid,
    ).update(is_deleted=True)


def validate_org_mcp_uuids(org: Organization, uuids: list[uuid_lib.UUID]) -> None:
    if not uuids:
        return
    linked = {str(value) for value in org_mcp_uuids(org)}
    missing = [str(value) for value in uuids if str(value) not in linked]
    if missing:
        raise ValidationError({"mcp_bindings": "One or more MCP servers do not belong to this organization."})


def sync_assistant_mcp_links(
    *,
    org: Organization,
    assistant_uuid: uuid_lib.UUID,
    created_by: User | None = None,
) -> None:
    data = sl_client.request_json("GET", f"/api/lens/assistants/{assistant_uuid}/")
    if not isinstance(data, dict):
        return
    bindings = data.get("mcp_bindings") or []
    if not isinstance(bindings, list):
        return
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        mcp = binding.get("mcp_server") if isinstance(binding.get("mcp_server"), dict) else {}
        uuid_str = mcp.get("uuid") or binding.get("mcp_uuid")
        if not uuid_str:
            continue
        register_org_mcp(
            org=org,
            sl_mcp_uuid=uuid_lib.UUID(str(uuid_str)),
            created_by=created_by,
        )
