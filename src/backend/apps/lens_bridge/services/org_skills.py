"""Organization-scoped SourceLens Skill ownership."""

from __future__ import annotations

import uuid as uuid_lib
from typing import Any

from django.contrib.auth import get_user_model
from rest_framework.exceptions import NotFound, ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import LensOrgSkillLink
from apps.lens_bridge.services import sl_client
from apps.lens_bridge.services.assistants import _org_prefix, _unwrap_list
from apps.lens_bridge.services.provisioning import _slugify_assistant
from apps.lens_bridge.services import skills as sl_skills

User = get_user_model()


def org_skill_links(org: Organization):
    return LensOrgSkillLink.objects.filter(
        organization=org,
        is_deleted=False,
    ).order_by("created_at", "id")


def org_skill_uuids(org: Organization) -> list[uuid_lib.UUID]:
    return list(org_skill_links(org).values_list("sl_skill_uuid", flat=True))


def register_org_skill(
    *,
    org: Organization,
    sl_skill_uuid: uuid_lib.UUID,
    created_by: User | None = None,
) -> LensOrgSkillLink:
    link, created = LensOrgSkillLink.objects.get_or_create(
        organization=org,
        sl_skill_uuid=sl_skill_uuid,
        defaults={"created_by": created_by},
    )
    if not created and link.is_deleted:
        link.is_deleted = False
        link.deleted_at = None
        if created_by is not None:
            link.created_by = created_by
        link.save(update_fields=["is_deleted", "deleted_at", "created_by", "updated_at"])
    return link


def require_org_skill(org: Organization, skill_uuid: uuid_lib.UUID) -> LensOrgSkillLink:
    link = org_skill_links(org).filter(sl_skill_uuid=skill_uuid).first()
    if link is None:
        raise NotFound("Skill not found for this organization.")
    return link


def skill_belongs_to_org(org: Organization, skill: dict[str, Any]) -> bool:
    slug = str(skill.get("slug") or "")
    return slug.startswith(f"{_org_prefix(org)}-")


def ensure_org_skill_link_for_row(
    *,
    org: Organization,
    skill: dict[str, Any],
    created_by: User | None = None,
) -> LensOrgSkillLink | None:
    uuid_str = str(skill.get("uuid") or "")
    if not uuid_str:
        return None
    if not skill_belongs_to_org(org, skill):
        return None
    return register_org_skill(
        org=org,
        sl_skill_uuid=uuid_lib.UUID(uuid_str),
        created_by=created_by,
    )


def list_org_skills(org: Organization) -> list[dict[str, Any]]:
    linked = {str(value) for value in org_skill_uuids(org)}
    raw = sl_client.request_json("GET", "/api/lens/admin/skills/")
    items = _unwrap_list(raw)
    rows: list[dict[str, Any]] = []
    for item in items:
        uuid_str = str(item.get("uuid") or "")
        if uuid_str in linked:
            rows.append(item)
            continue
        link = ensure_org_skill_link_for_row(org=org, skill=item)
        if link is not None:
            rows.append(item)
    rows.sort(key=lambda row: (row.get("name") or "").lower())
    return rows


def get_org_skill(org: Organization, skill_uuid: uuid_lib.UUID) -> dict[str, Any]:
    link = org_skill_links(org).filter(sl_skill_uuid=skill_uuid).first()
    data = sl_skills.get_skill(skill_uuid)
    if link is None:
        if skill_belongs_to_org(org, data):
            register_org_skill(org=org, sl_skill_uuid=skill_uuid)
        else:
            raise NotFound("Skill not found for this organization.")
    return data


def create_org_skill(
    org: Organization,
    body: dict[str, Any],
    *,
    created_by: User | None = None,
) -> dict[str, Any]:
    payload = dict(body)
    slug = str(payload.get("slug") or "").strip()
    if not slug:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValidationError({"name": "Name is required."})
        payload["slug"] = _slugify_assistant(name, org)
    data = sl_skills.create_skill(payload)
    skill_uuid = data.get("uuid")
    if not skill_uuid:
        raise sl_client.LensBridgeError("SourceLens skill create returned no uuid.")
    register_org_skill(
        org=org,
        sl_skill_uuid=uuid_lib.UUID(str(skill_uuid)),
        created_by=created_by,
    )
    return data


def update_org_skill(
    org: Organization,
    skill_uuid: uuid_lib.UUID,
    body: dict[str, Any],
) -> dict[str, Any]:
    require_org_skill(org, skill_uuid)
    return sl_skills.update_skill(skill_uuid, body)


def delete_org_skill(org: Organization, skill_uuid: uuid_lib.UUID) -> None:
    require_org_skill(org, skill_uuid)
    sl_skills.delete_skill(skill_uuid)
    LensOrgSkillLink.objects.filter(
        organization=org,
        sl_skill_uuid=skill_uuid,
    ).update(is_deleted=True)


def validate_org_skill_uuids(org: Organization, uuids: list[uuid_lib.UUID]) -> None:
    if not uuids:
        return
    linked = {str(value) for value in org_skill_uuids(org)}
    missing = [str(value) for value in uuids if str(value) not in linked]
    if missing:
        raise ValidationError({"skill_bindings": "One or more skills do not belong to this organization."})


def sync_assistant_skill_links(
    *,
    org: Organization,
    assistant_uuid: uuid_lib.UUID,
    created_by: User | None = None,
) -> None:
    data = sl_client.request_json("GET", f"/api/lens/assistants/{assistant_uuid}/")
    if not isinstance(data, dict):
        return
    bindings = data.get("skill_bindings") or []
    if not isinstance(bindings, list):
        return
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        skill = binding.get("skill") if isinstance(binding.get("skill"), dict) else {}
        uuid_str = skill.get("uuid") or binding.get("skill_uuid")
        if not uuid_str:
            continue
        register_org_skill(
            org=org,
            sl_skill_uuid=uuid_lib.UUID(str(uuid_str)),
            created_by=created_by,
        )
