"""Orchestration helpers for SourceLens Assistant / LensNode provisioning."""

from __future__ import annotations

import logging
import posixpath
import re
import time
import uuid
from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensOrgLink,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import ingest_policy, org_models, retrieval_policy, sl_client
from apps.node.models.base import NodeRole
from apps.node.models.node import Node

logger = logging.getLogger(__name__)

_LENSNODE_DIR_WAIT_SECONDS = 60.0
_LENSNODE_DIR_POLL_SECONDS = 0.5
LENSNODE_PROVISION_CLAIM_TTL_SECONDS = 300

ANALYSIS_MODE_AGENT_ROUNDS = {
    "fast": "fast",
    "standard": "balanced",
    "deep": "deep",
}

ANALYSIS_TYPE_TASKS = {
    "knowledge_qa": "knowledge_qa",
    "code_analysis": "code_analysis",
}


def normalize_analysis_type(value: str | None) -> str:
    """Return the stable HFL analysis type used for new Chat requests."""

    normalized = str(value or "knowledge_qa").strip().lower()
    if normalized not in ANALYSIS_TYPE_TASKS:
        raise ValidationError({"analysis_type": "Select a supported analysis type."})
    return normalized


def analysis_types_for_tasks(tasks: list[dict[str, Any]] | None) -> list[str]:
    """Map the SourceLens task snapshot to HFL's user-facing choices."""

    supported: list[str] = []
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        name = str(task.get("name") or task.get("task") or "").strip()
        for analysis_type, task_name in ANALYSIS_TYPE_TASKS.items():
            if name == task_name:
                if analysis_type not in supported:
                    supported.append(analysis_type)
    return supported


def analysis_types_for_gateway(link: LensGatewayLink) -> list[str]:
    """Return HFL analysis choices advertised by one Data Gateway."""

    # Gateway links created before task snapshots were introduced do not have
    # enough local information to advertise every task. Keep the historical
    # Knowledge Q&A default available; the live SourceLens request remains the
    # final capability check during Assistant provisioning.
    if not has_gateway_task_snapshot(link):
        return ["knowledge_qa"]
    snapshot = sl_lensnode_snapshot_from_link(link)
    return analysis_types_for_tasks(snapshot.get("sl_tasks") or [])


def has_gateway_task_snapshot(link: LensGatewayLink) -> bool:
    """Return whether this link has a SourceLens task snapshot to evaluate."""

    config = link.config_json or {}
    snapshot = config.get("sl_lensnode_snapshot")
    return isinstance(snapshot, dict) and "sl_tasks" in snapshot


def validate_analysis_type_for_gateway(
    link: LensGatewayLink,
    analysis_type: str | None,
) -> str:
    """Validate a Chat choice against the selected Gateway capability snapshot."""

    normalized = normalize_analysis_type(analysis_type)
    supported = analysis_types_for_gateway(link)
    if normalized in supported:
        return normalized
    # Older Gateway records may not have a task snapshot yet. Preserve the
    # long-standing Knowledge Q&A default; live Assistant creation validates it.
    if not has_gateway_task_snapshot(link) and normalized == "knowledge_qa":
        return normalized
    raise ValidationError(
        {"analysis_type": "The selected Data Gateway does not support this analysis type."}
    )


def agent_rounds_for_analysis_mode(mode: str | None) -> str:
    """Map HFL's stable product choices to SourceLens execution values."""

    return ANALYSIS_MODE_AGENT_ROUNDS.get(str(mode or "standard"), "balanced")


class LensNodeProvisionBusyError(sl_client.LensBridgeError):
    """Raised when another caller owns the durable provisioning lease."""

    status_code = 409
    default_detail = "LensNode provisioning is already in progress."
    default_code = "lensnode_provision_busy"


def get_or_create_org_link(org: Organization) -> LensOrgLink:
    link, _ = LensOrgLink.objects.get_or_create(organization=org)
    return link


def _slugify_assistant(name: str, org: Organization) -> str:
    org_link = get_or_create_org_link(org)
    prefix = org_link.resolved_prefix()
    base = slugify(name) or "source"
    slug = f"{prefix}-{base}"[:160]
    slug = re.sub(r"[^a-z0-9-]", "-", slug.lower())
    return slug.strip("-") or f"{prefix}-source"


def assistant_slug_for_ks(*, org: Organization, ks: LensKnowledgeSource) -> str:
    """Return a deterministic, collision-resistant slug for one HFL KS."""
    suffix = f"-ks-{ks.id}"
    max_prefix_length = max(1, 160 - len(suffix) - 2)
    org_prefix = (
        get_or_create_org_link(org)
        .resolved_prefix()[:max_prefix_length]
        .strip("-")
        or "org"
    )
    max_base_length = max(1, 160 - len(org_prefix) - len(suffix) - 1)
    base = (slugify(ks.name) or "source")[:max_base_length]
    base = re.sub(r"[^a-z0-9-]", "-", base.lower()).strip("-") or "source"
    return f"{org_prefix}-{base}{suffix}"


def default_model_refs_for_org(
    org: Organization,
    *,
    tenant_rows: list[dict[str, Any]] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve Agent and multimodal defaults from organization-owned links.

    HFL role pointers are authoritative. SourceLens's process-wide
    ``is_default`` flag is intentionally ignored because it is not tenant
    scoped.
    """

    from apps.lens_bridge.services import platform_lens

    platform_org = platform_lens.get_or_create_platform_org()
    tenant_rows = (
        tenant_rows
        if tenant_rows is not None
        else org_models.active_llm_configs(org=org)
    )
    platform_rows = (
        tenant_rows
        if org.pk == platform_org.pk
        else org_models.active_llm_configs(org=platform_org)
    )
    tenant_active = {
        str(row.get("uuid") or "")
        for row in tenant_rows
        if row.get("uuid") and not row.get("is_deployment_history")
    }
    platform_active = {
        str(row.get("uuid") or "")
        for row in platform_rows
        if row.get("uuid") and not row.get("is_deployment_history")
    }

    org_defaults = org_models.ensure_org_model_defaults(org)
    platform_defaults = (
        org_defaults
        if org.pk == platform_org.pk
        else org_models.ensure_org_model_defaults(platform_org)
    )

    agent_ref: str | None = None
    if org.pk != platform_org.pk and org_defaults.default_agent_model_ref:
        candidate = str(org_defaults.default_agent_model_ref)
        if candidate in tenant_active:
            agent_ref = candidate
    if agent_ref is None and platform_defaults.default_agent_model_ref:
        candidate = str(platform_defaults.default_agent_model_ref)
        if candidate in platform_active:
            agent_ref = candidate
    if agent_ref is None:
        managed_agent = org_models.deployment_managed_model_uuid(
            platform_org,
            role="agent",
        )
        if managed_agent is not None and str(managed_agent) in platform_active:
            agent_ref = str(managed_agent)

    multimodal_ref: str | None = None
    if (
        org.pk != platform_org.pk
        and org_defaults.default_multimodal_model_ref
    ):
        candidate = str(org_defaults.default_multimodal_model_ref)
        if candidate in tenant_active:
            multimodal_ref = candidate
    if (
        multimodal_ref is None
        and platform_defaults.default_multimodal_model_ref
    ):
        candidate = str(platform_defaults.default_multimodal_model_ref)
        if candidate in platform_active:
            multimodal_ref = candidate
    if multimodal_ref is None:
        managed_multimodal = org_models.deployment_managed_model_uuid(
            platform_org,
            role="multimodal",
        )
        if (
            managed_multimodal is not None
            and str(managed_multimodal) in platform_active
        ):
            multimodal_ref = str(managed_multimodal)

    return agent_ref, multimodal_ref


def configured_default_model_refs_for_org(
    org: Organization,
) -> tuple[str | None, str | None]:
    """Resolve HFL-owned model defaults without calling SourceLens."""

    from apps.lens_bridge.services import platform_lens

    platform_org = platform_lens.get_or_create_platform_org()
    org_defaults = get_or_create_org_link(org)
    platform_defaults = (
        org_defaults
        if org.pk == platform_org.pk
        else get_or_create_org_link(platform_org)
    )

    def _owned_default(owner: Organization, model_ref) -> str | None:
        if not model_ref:
            return None
        exists = org_models.org_model_links(owner).filter(
            sl_config_uuid=model_ref,
            is_deployment_history=False,
        ).exists()
        return str(model_ref) if exists else None

    agent_ref = None
    if org.pk != platform_org.pk:
        agent_ref = _owned_default(org, org_defaults.default_agent_model_ref)
    if agent_ref is None:
        agent_ref = _owned_default(
            platform_org,
            platform_defaults.default_agent_model_ref,
        )
    if agent_ref is None:
        managed_agent = org_models.deployment_managed_model_uuid(
            platform_org,
            role="agent",
        )
        agent_ref = str(managed_agent) if managed_agent is not None else None

    multimodal_ref = None
    if org.pk != platform_org.pk:
        multimodal_ref = _owned_default(
            org,
            org_defaults.default_multimodal_model_ref,
        )
    if multimodal_ref is None:
        multimodal_ref = _owned_default(
            platform_org,
            platform_defaults.default_multimodal_model_ref,
        )
    if multimodal_ref is None:
        managed_multimodal = org_models.deployment_managed_model_uuid(
            platform_org,
            role="multimodal",
        )
        multimodal_ref = (
            str(managed_multimodal)
            if managed_multimodal is not None
            else None
        )
    return agent_ref, multimodal_ref


def default_model_ref_for_org(org: Organization) -> str | None:
    """Resolve the effective Agent model for an organization."""

    return default_model_refs_for_org(org)[0]


def default_multimodal_model_ref_for_org(
    org: Organization,
) -> str | None:
    """Resolve the effective multimodal model for an organization."""

    return default_model_refs_for_org(org)[1]


def get_gateway_link(org: Organization, gateway_id: int) -> LensGatewayLink:
    """Resolve an existing gateway link owned by ``org``.

    Platform gateway access is intentionally handled by the Copilot gateway
    execution service; tenant-facing APIs must never fall back across orgs.
    """
    existing = (
        LensGatewayLink.objects.filter(organization=org, gateway_id=gateway_id)
        .select_related("gateway")
        .first()
    )
    if existing is not None:
        return existing
    raise ValidationError({"gateway_id": "Data gateway link not found."})


def require_gateway_node(org: Organization, gateway_id: int) -> Node:
    node = Node.objects.filter(
        organization=org,
        id=gateway_id,
        role=NodeRole.GATEWAY,
        is_deleted=False,
    ).first()
    if node is not None:
        return node
    raise ValidationError({"gateway_id": "Data gateway not found."})


def _lensnode_matches_workspace(
    lensnode_data: dict[str, Any],
    *,
    lensnode_uuid: uuid.UUID,
    workspace_root: str,
    selected_dir: str | None = None,
) -> bool:
    reported_uuid = str(lensnode_data.get("uuid") or "").strip().lower()
    reported_status = str(lensnode_data.get("status") or "").strip().lower()
    reported_root = posixpath.normpath(
        str(lensnode_data.get("workspace_path") or "").strip()
    )
    expected_root = posixpath.normpath(str(workspace_root or "").strip())
    matches = (
        reported_uuid == str(lensnode_uuid).lower()
        and reported_status == "online"
        and reported_root == expected_root
    )
    if not matches or not selected_dir:
        return matches
    expected_dir = posixpath.normpath(str(selected_dir).strip())
    available_dirs = lensnode_data.get("available_dirs") or []
    reported_dirs = {
        posixpath.normpath(
            str(
                item if isinstance(item, str) else item.get("path") or ""
            ).strip()
        )
        for item in available_dirs
        if isinstance(item, (str, dict))
    }
    return expected_dir in reported_dirs


def wait_for_lensnode_ready(
    *,
    lensnode_uuid: uuid.UUID,
    workspace_root: str,
    selected_dir: str | None = None,
    timeout_s: float = _LENSNODE_DIR_WAIT_SECONDS,
) -> None:
    """Wait for the LensNode and, when provided, one selectable directory."""

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        data = sl_client.request_json("GET", f"/api/lens/admin/lensnodes/{lensnode_uuid}/")
        if _lensnode_matches_workspace(
            data,
            lensnode_uuid=lensnode_uuid,
            workspace_root=workspace_root,
            selected_dir=selected_dir,
        ):
            return
        time.sleep(_LENSNODE_DIR_POLL_SECONDS)
    detail = (
        f"LensNode did not advertise selectable workspace: {selected_dir}."
        if selected_dir
        else f"LensNode did not become ready at workspace root: {workspace_root}."
    )
    raise ValidationError(
        {"workspace": f"{detail} Ensure the AI engine is online and retry."}
    )


def ensure_ks_workspace_on_gateway(
    *,
    org: Organization,
    gateway: Node,
    gateway_link: LensGatewayLink,
    workspace_binding: LensWorkspaceBinding,
) -> None:
    """Create the KS workspace directory on the gateway host and wait for LensNode."""

    from apps.node.services.internal.agent_task import run_agent_task_sync

    lensnode_uuid = gateway_link.sl_lensnode_uuid
    if not lensnode_uuid:
        raise ValidationError({"gateway_id": "LensNode is not linked to this gateway."})

    from apps.lens_bridge.services.gateway_execution import (
        context_for_gateway_link,
        workspace_identity_payload,
    )

    context = context_for_gateway_link(
        tenant_organization=org,
        gateway_link=gateway_link,
    )
    if context.gateway.id != gateway.id:
        raise ValidationError({"gateway_id": "Data gateway link does not match the execution node."})
    root = gateway_link.resolved_workspace_root()
    workspace_path = workspace_binding.resolved_path()
    if (
        workspace_binding.gateway_link_id != gateway_link.id
        or workspace_binding.execution_node_id != gateway.id
        or workspace_binding.execution_organization_id != context.execution_organization.id
        or workspace_binding.workspace_root != root
    ):
        raise ValidationError({"workspace": "Workspace execution binding is inconsistent."})
    outcome = run_agent_task_sync(
        org=context.execution_organization,
        node_id=gateway.id,
        kind="lens.ks.prepare",
        payload={
            "path": workspace_path,
            **workspace_identity_payload(workspace_binding),
        },
        correlation_type="lens_knowledge_source",
        requesting_organization_id=org.id,
        wait_timeout_seconds=60,
    )
    if not outcome.ok:
        detail = outcome.task.last_error or "Failed to prepare knowledge source workspace on gateway."
        workspace_binding.identity_status = LensWorkspaceBinding.IdentityStatus.ERROR
        workspace_binding.last_error = detail
        workspace_binding.save(
            update_fields=["identity_status", "last_error", "updated_at"]
        )
        raise ValidationError({"gateway": detail})

    workspace_binding.identity_status = LensWorkspaceBinding.IdentityStatus.READY
    workspace_binding.last_error = ""
    workspace_binding.save(
        update_fields=["identity_status", "last_error", "updated_at"]
    )

    wait_for_lensnode_ready(
        lensnode_uuid=lensnode_uuid,
        workspace_root=root,
        selected_dir=workspace_path,
    )


def pick_lensnode_task(
    lensnode_uuid: uuid.UUID,
    *,
    analysis_type: str | None = None,
) -> str:
    """Resolve the requested HFL analysis type to a SourceLens task."""

    requested_type = normalize_analysis_type(analysis_type)
    data = sl_client.request_json("GET", f"/api/lens/admin/lensnodes/{lensnode_uuid}/")
    tasks = data.get("tasks") or []
    if not tasks:
        raise ValidationError({"lensnode": "LensNode has no available tasks."})
    selected_task = ANALYSIS_TYPE_TASKS[requested_type]
    for task in tasks:
        if isinstance(task, dict):
            name = str(task.get("name") or task.get("task") or "").strip()
        else:
            name = str(task).strip()
        if name == selected_task:
            return name
    raise ValidationError(
        {"analysis_type": "The selected Data Gateway does not support this analysis type."}
    )


def indexed_dirs_for_ks(ks: LensKnowledgeSource) -> list[dict[str, str]]:
    from apps.lens_bridge.services.knowledge_source_sync import indexed_dir_paths

    return [{"path": path} for path in indexed_dir_paths(ks)]


def assistant_uuid_for_ks(ks: LensKnowledgeSource) -> uuid.UUID | None:
    """Return the user-linked Assistant for this knowledge source, if any."""
    if ks.sl_assistant_uuid:
        return ks.sl_assistant_uuid
    from apps.lens_bridge.models import LensAssistantLink

    link = (
        LensAssistantLink.objects.filter(
            organization_id=ks.organization_id,
            knowledge_source_id=ks.id,
        )
        .only("sl_assistant_uuid")
        .first()
    )
    if link is not None:
        return link.sl_assistant_uuid
    return None


def _assistant_is_chat_managed(
    *,
    org: Organization,
    assistant_uuid: uuid.UUID,
) -> bool:
    """Return whether HFL Chat owns this SourceLens assistant lifecycle."""
    from apps.lens_bridge.models import LensAssistantLink

    return LensAssistantLink.objects.filter(
        organization=org,
        sl_assistant_uuid=assistant_uuid,
        lifecycle_owner=LensAssistantLink.LifecycleOwner.CHAT,
    ).exists()


def sync_linked_assistant_for_ks(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    gateway_link: LensGatewayLink,
) -> uuid.UUID | None:
    """Push KS workspace/index settings to a linked Assistant; never auto-create one."""
    assistant_uuid = assistant_uuid_for_ks(ks)
    if not assistant_uuid:
        return None
    if ks.sl_assistant_uuid != assistant_uuid:
        ks.sl_assistant_uuid = assistant_uuid
        ks.save(update_fields=["sl_assistant_uuid", "updated_at"])
    update_sl_assistant_for_ks(org=org, ks=ks, gateway_link=gateway_link)
    return assistant_uuid


def update_sl_assistant_for_ks(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    gateway_link: LensGatewayLink,
) -> None:
    if not ks.sl_assistant_uuid:
        raise ValidationError({"knowledge_source": "Knowledge source has no linked assistant."})
    lensnode_uuid = gateway_link.sl_lensnode_uuid
    if not lensnode_uuid:
        raise ValidationError({"gateway_id": "LensNode is not linked to this gateway."})

    selected_dirs = indexed_dirs_for_ks(ks)
    policy = ingest_policy.normalize_ingest_policy(ks.ingest_policy_json)
    data = sl_client.request_json("GET", f"/api/lens/assistants/{ks.sl_assistant_uuid}/")
    settings = dict(data.get("settings") or {})
    settings["ingestion"] = {
        "conversion": ingest_policy.conversion_payload_for_sl(policy),
    }
    # Chat-owned workspaces are user restore data: keep every restored path
    # searchable. Manual Insight assistants keep operator-configured excludes.
    if _assistant_is_chat_managed(org=org, assistant_uuid=ks.sl_assistant_uuid):
        settings["retrieval_policy"] = retrieval_policy.managed_chat_retrieval_policy()
    sl_client.request_json(
        "PATCH",
        f"/api/lens/assistants/{ks.sl_assistant_uuid}/",
        json_body={
            "selected_dirs": selected_dirs,
            "settings": settings,
        },
    )


def create_sl_assistant_for_ks(
    *,
    org: Organization,
    ks: LensKnowledgeSource,
    gateway_link: LensGatewayLink,
    model_ref: str | uuid.UUID | None = None,
    multimodal_model_ref: str | uuid.UUID | None = None,
    analysis_type: str | None = None,
    analysis_mode: str | None = None,
    slug: str | None = None,
) -> uuid.UUID:
    """Create the remote Assistant without mutating HFL ownership state."""
    lensnode_uuid = gateway_link.sl_lensnode_uuid
    if not lensnode_uuid:
        raise ValidationError({"gateway_id": "LensNode is not linked to this gateway."})

    model_ref = str(model_ref or default_model_ref_for_org(org) or "")
    if not model_ref:
        raise ValidationError(
            {"model": "Set a default AI model in Insights → AI Models before creating knowledge sources."}
        )

    workspace_path = (ks.workspace_path_on_lensnode or "").strip()
    if not workspace_path:
        raise ValidationError({"workspace": "Knowledge source workspace is not prepared."})
    selected_task = pick_lensnode_task(
        lensnode_uuid,
        analysis_type=analysis_type,
    )
    resolved_slug = (slug or "").strip() or _slugify_assistant(ks.name, org)

    selected_dirs = indexed_dirs_for_ks(ks)
    if not selected_dirs:
        selected_dirs = [{"path": workspace_path}]

    payload: dict[str, Any] = {
        "name": ks.name,
        "slug": resolved_slug,
        "lensnode_uuid": str(lensnode_uuid),
        "selected_task": selected_task,
        "selected_dirs": selected_dirs,
        "agent_model_ref": model_ref,
        "agent_rounds": agent_rounds_for_analysis_mode(analysis_mode),
        "visibility": "private",
        "status": "active",
    }
    if multimodal_model_ref:
        payload["multimodal_model_ref"] = str(multimodal_model_ref)
    policy = ingest_policy.normalize_ingest_policy(ks.ingest_policy_json)
    payload["settings"] = {
        "ingestion": {
            "conversion": ingest_policy.conversion_payload_for_sl(policy),
        },
        # Chat workspace is user data: keep restored dotfiles searchable.
        "retrieval_policy": retrieval_policy.managed_chat_retrieval_policy(),
    }
    data = sl_client.request_json("POST", "/api/lens/assistants/", json_body=payload)
    assistant_uuid = data.get("uuid")
    if not assistant_uuid:
        raise sl_client.LensBridgeError("SourceLens assistant create returned no uuid.")
    return uuid.UUID(str(assistant_uuid))


def sync_assistant_agent_model(
    *,
    ks: LensKnowledgeSource,
    model_ref: uuid.UUID,
    assistant_uuid: uuid.UUID | None = None,
) -> None:
    """Push agent model selection to the linked SourceLens Assistant."""

    sync_assistant_execution_config(
        ks=ks,
        model_ref=model_ref,
        assistant_uuid=assistant_uuid,
    )


def sync_assistant_execution_config(
    *,
    ks: LensKnowledgeSource,
    model_ref: uuid.UUID | str | None = None,
    analysis_mode: str | None = None,
    analysis_type: str | None = None,
    assistant_uuid: uuid.UUID | None = None,
) -> None:
    """Push Chat-owned execution settings through SourceLens's Assistant API."""

    target = assistant_uuid or assistant_uuid_for_ks(ks)
    if not target:
        raise ValidationError({"knowledge_source": "Knowledge source has no linked assistant."})
    if ks.sl_assistant_uuid != target:
        ks.sl_assistant_uuid = target
        ks.save(update_fields=["sl_assistant_uuid", "updated_at"])
    payload: dict[str, str] = {}
    if model_ref is not None:
        payload["agent_model_ref"] = str(model_ref)
    if analysis_mode is not None:
        payload["agent_rounds"] = agent_rounds_for_analysis_mode(analysis_mode)
    if analysis_type is not None:
        payload["selected_task"] = ANALYSIS_TYPE_TASKS[
            normalize_analysis_type(analysis_type)
        ]
    if not payload:
        return
    sl_client.request_json(
        "PATCH",
        f"/api/lens/assistants/{target}/",
        json_body=payload,
    )


def refresh_ks_status_from_sl(ks: LensKnowledgeSource) -> LensKnowledgeSource:
    if not ks.sl_assistant_uuid:
        return ks
    try:
        data = sl_client.request_json("GET", f"/api/lens/assistants/{ks.sl_assistant_uuid}/")
    except sl_client.LensBridgeError as exc:
        ks.status = LensKnowledgeSource.Status.ERROR
        ks.status_detail = str(exc.detail)
        ks.save(update_fields=["status", "status_detail", "updated_at"])
        return ks

    if data.get("status") == "disabled":
        ks.status = LensKnowledgeSource.Status.PAUSED
    else:
        model_check = (data.get("settings") or {}).get("_model_check") or {}
        agent_check = (model_check.get("agent_model_ref") or {}).get("status")
        if agent_check == "ok":
            if ks.status != LensKnowledgeSource.Status.DEGRADED:
                ks.status = LensKnowledgeSource.Status.READY
        elif agent_check:
            ks.status = LensKnowledgeSource.Status.ERROR
            ks.status_detail = str(agent_check)
        elif ks.status == LensKnowledgeSource.Status.SYNCING:
            pass
        else:
            ks.status = LensKnowledgeSource.Status.SYNCING
            ks.status_detail = ks.status_detail or "Indexing in progress…"
    ks.save(update_fields=["status", "status_detail", "updated_at"])
    return ks


def _lensnode_lookup_name(
    *,
    link: LensGatewayLink,
    gateway: Node,
    requested_name: str | None,
) -> str:
    """Return a stable remote identity unique to one HFL Gateway link."""

    suffix = f"-hfl-gateway-link-{link.id}"
    max_base_length = max(1, 160 - len(suffix))
    base = slugify(requested_name or gateway.name or "gateway")
    base = (base or "gateway")[:max_base_length].strip("-") or "gateway"
    return f"{base}{suffix}"


def _source_lens_lensnodes() -> list[dict[str, Any]]:
    """Return all SourceLens LensNodes through bounded pagination."""

    rows: list[dict[str, Any]] = []
    seen_pages: set[tuple[str, ...]] = set()
    page_size = 100
    for page in range(1, 1001):
        raw = sl_client.request_json(
            "GET",
            "/api/lens/admin/lensnodes/",
            params={"page": page, "page_size": page_size},
        )
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("results", raw.get("items", []))
        else:
            items = []
        page_rows = [item for item in items if isinstance(item, dict)]
        rows.extend(page_rows)
        if isinstance(raw, list) or not page_rows or len(page_rows) < page_size:
            return rows
        signature = tuple(
            str(item.get("uuid") or item.get("name") or "")
            for item in page_rows
        )
        if signature in seen_pages:
            raise sl_client.LensBridgeError(
                "SourceLens pagination did not advance while finding a LensNode."
            )
        seen_pages.add(signature)
    raise sl_client.LensBridgeError(
        "SourceLens pagination limit reached while finding a LensNode."
    )


def _find_source_lens_lensnode(*, lookup_name: str) -> dict[str, Any] | None:
    matches = [
        row
        for row in _source_lens_lensnodes()
        if str(row.get("name") or "").strip() == lookup_name
    ]
    if len(matches) > 1:
        raise sl_client.LensBridgeError(
            f"SourceLens returned multiple LensNodes named {lookup_name!r}."
        )
    return matches[0] if matches else None


@transaction.atomic
def _claim_lensnode_provision(
    *,
    link_id: int,
    gateway: Node,
    requested_name: str | None,
) -> tuple[LensGatewayLink, str, str]:
    """Persist remote-create intent and acquire the provisioning lease."""

    link = LensGatewayLink.objects.select_for_update().get(pk=link_id)
    if link.sl_lensnode_uuid:
        return link, "", "ready"
    now = timezone.now()
    if (
        link.lensnode_provision_claim_token
        and link.lensnode_provision_claimed_at
        and link.lensnode_provision_claimed_at
        > now - timedelta(seconds=LENSNODE_PROVISION_CLAIM_TTL_SECONDS)
    ):
        return link, "", "busy"

    state = dict(link.lensnode_provision_state_json or {})
    lookup_name = str(state.get("lookup_name") or "").strip()
    if not lookup_name:
        lookup_name = _lensnode_lookup_name(
            link=link,
            gateway=gateway,
            requested_name=requested_name,
        )
    claim_token = uuid.uuid4()
    state.update(
        {
            "lookup_name": lookup_name,
            "status": "provisioning",
            "last_error": "",
            "updated_at": now.isoformat(),
        }
    )
    link.lensnode_provision_state_json = state
    link.lensnode_provision_attempts += 1
    link.lensnode_provision_claim_token = claim_token
    link.lensnode_provision_claimed_at = now
    link.save(
        update_fields=[
            "lensnode_provision_state_json",
            "lensnode_provision_attempts",
            "lensnode_provision_claim_token",
            "lensnode_provision_claimed_at",
            "updated_at",
        ]
    )
    return link, str(claim_token), "claimed"


@transaction.atomic
def _complete_lensnode_provision(
    *,
    link_id: int,
    claim_token: str,
    lensnode_uuid: uuid.UUID,
    token: str,
) -> LensGatewayLink:
    """Commit recovered credentials only while the caller owns the lease."""

    link = (
        LensGatewayLink.objects.select_for_update()
        .filter(
            pk=link_id,
            lensnode_provision_claim_token=claim_token,
            sl_lensnode_uuid__isnull=True,
        )
        .first()
    )
    if link is None:
        raise LensNodeProvisionBusyError(
            "LensNode provisioning lease was lost before completion."
        )
    config = dict(link.config_json or {})
    config["lensnode_token_issued"] = True
    config["lensnode_token"] = token
    state = dict(link.lensnode_provision_state_json or {})
    state.update(
        {
            "remote_uuid": str(lensnode_uuid),
            "status": "ready",
            "last_error": "",
            "updated_at": timezone.now().isoformat(),
        }
    )
    link.sl_lensnode_uuid = lensnode_uuid
    link.sidecar_status = LensGatewayLink.SidecarStatus.OFFLINE
    link.config_json = config
    link.lensnode_provision_state_json = state
    link.lensnode_provision_claim_token = None
    link.lensnode_provision_claimed_at = None
    link.save(
        update_fields=[
            "sl_lensnode_uuid",
            "sidecar_status",
            "config_json",
            "lensnode_provision_state_json",
            "lensnode_provision_claim_token",
            "lensnode_provision_claimed_at",
            "updated_at",
        ]
    )
    return link


def _record_lensnode_provision_failure(
    *,
    link_id: int,
    claim_token: str,
    error: Exception,
) -> None:
    """Retain a failed lease until expiry to avoid timeout duplication."""

    with transaction.atomic():
        link = (
            LensGatewayLink.objects.select_for_update()
            .filter(pk=link_id, lensnode_provision_claim_token=claim_token)
            .first()
        )
        if link is None:
            return
        state = dict(link.lensnode_provision_state_json or {})
        state.update(
            {
                "status": "error",
                "last_error": str(error)[:1000],
                "updated_at": timezone.now().isoformat(),
            }
        )
        link.lensnode_provision_state_json = state
        link.save(
            update_fields=["lensnode_provision_state_json", "updated_at"]
        )


def _provision_source_lens_lensnode(
    *,
    link: LensGatewayLink,
    gateway: Node,
    requested_name: str | None,
) -> LensGatewayLink:
    """Create or recover one SourceLens LensNode under a durable lease."""

    claimed_link, claim_token, claim_status = _claim_lensnode_provision(
        link_id=link.id,
        gateway=gateway,
        requested_name=requested_name,
    )
    if claim_status == "ready":
        return claimed_link
    if claim_status == "busy":
        raise LensNodeProvisionBusyError()

    state = dict(claimed_link.lensnode_provision_state_json or {})
    lookup_name = str(state["lookup_name"])
    try:
        remote = _find_source_lens_lensnode(lookup_name=lookup_name)
        if remote is None:
            remote = sl_client.request_json(
                "POST",
                "/api/lens/admin/lensnodes/",
                json_body={"name": lookup_name},
            )
            token = str(remote.get("token") or "")
        else:
            remote_uuid = remote.get("uuid")
            if not remote_uuid:
                raise sl_client.LensBridgeError(
                    "Recovered SourceLens LensNode has no uuid."
                )
            if (
                LensGatewayLink.objects.exclude(pk=link.id)
                .filter(sl_lensnode_uuid=remote_uuid)
                .exists()
            ):
                raise sl_client.LensBridgeError(
                    "Recovered SourceLens LensNode is already bound to another "
                    "HFL data gateway."
                )
            issued = sl_client.request_json(
                "POST",
                f"/api/lens/admin/lensnodes/{remote_uuid}/issue-token/",
            )
            token = str(issued.get("token") or "")
        remote_uuid = remote.get("uuid")
        if not remote_uuid or not token:
            raise sl_client.LensBridgeError(
                "SourceLens LensNode provisioning returned incomplete credentials."
            )
        return _complete_lensnode_provision(
            link_id=link.id,
            claim_token=claim_token,
            lensnode_uuid=uuid.UUID(str(remote_uuid)),
            token=token,
        )
    except Exception as exc:
        _record_lensnode_provision_failure(
            link_id=link.id,
            claim_token=claim_token,
            error=exc,
        )
        raise


@transaction.atomic
def _resolve_gateway_link_identity(
    *,
    org: Organization,
    gateway: Node,
    owner_user,
    normalized_scope: str | None,
) -> LensGatewayLink:
    """Create or verify an immutable Gateway identity under a row lock."""

    existing = (
        LensGatewayLink.objects.select_for_update()
        .filter(organization=org, gateway=gateway)
        .first()
    )
    desired_scope = (
        existing.scope
        if existing is not None and normalized_scope is None
        else normalized_scope or LensGatewayLink.GatewayScope.USER
    )
    if (
        desired_scope == LensGatewayLink.GatewayScope.USER
        and owner_user is None
        and existing is None
    ):
        raise ValidationError(
            {"owner_user": "Private Data Gateway requires an owner."}
        )
    desired_origin = (
        existing.origin
        if existing is not None and normalized_scope is None
        else (
            LensGatewayLink.Origin.PLATFORM
            if desired_scope == LensGatewayLink.GatewayScope.PLATFORM
            else LensGatewayLink.Origin.USER
        )
    )
    is_platform = desired_scope == LensGatewayLink.GatewayScope.PLATFORM
    if existing is None:
        if is_platform:
            from apps.subscription.services.internal.public_gateway_count import (
                assert_public_gateway_count_available,
            )
            from common.errors import AppError

            try:
                assert_public_gateway_count_available(additional=1)
            except AppError:
                # A concurrent retry for this same Gateway may have created the
                # link while this transaction waited for the instance pool lock.
                # That is not additional consumption, so continue with the
                # committed identity and let the immutable-scope checks below
                # validate it. A genuinely different Gateway remains rejected.
                existing = (
                    LensGatewayLink.objects.select_for_update()
                    .filter(organization=org, gateway=gateway)
                    .first()
                )
                if existing is None:
                    raise
        if existing is not None:
            link = existing
            created = False
        else:
            link, created = LensGatewayLink.objects.get_or_create(
                organization=org,
                gateway=gateway,
                defaults={
                    "workspace_root": f"/workspace/org-{org.id}/data",
                    "owner_user": None if is_platform else owner_user,
                    "scope": desired_scope,
                    "origin": desired_origin,
                    # Infra capacity is set by Platform Ops; unlimited until configured.
                    "capacity_bytes": -1,
                },
            )
        if created:
            return link
        existing = (
            link
            if existing is not None
            else LensGatewayLink.objects.select_for_update().get(pk=link.pk)
        )

    link = existing
    requested_owner_id = getattr(owner_user, "id", None)
    if (
        not is_platform
        and link.scope == desired_scope
        and requested_owner_id is not None
        and link.owner_user_id != requested_owner_id
    ):
        raise ValidationError(
            {"owner_user": "Private Data Gateway belongs to another user."}
        )
    if link.scope != desired_scope:
        raise ValidationError(
            {
                "scope": (
                    "Data gateway scope is immutable after registration. "
                    "Uninstall and register the data gateway again to change it."
                )
            }
        )
    return link


def ensure_lensnode_for_gateway(
    *,
    org: Organization,
    gateway: Node,
    name: str | None = None,
    owner_user=None,
    scope: str | None = None,
) -> LensGatewayLink:
    """Idempotently associate a SourceLens LensNode with an HFL data gateway."""
    if gateway.role != NodeRole.GATEWAY:
        raise ValidationError({"gateway_id": "Node is not a data gateway."})
    if gateway.organization_id != org.id:
        raise ValidationError(
            {"gateway_id": "Data gateway belongs to another organization."}
        )

    from apps.node.services.internal.local_platform_gateway import (
        is_local_platform_gateway_metadata,
    )

    normalized_scope = (scope or "").strip().lower() or None
    if normalized_scope not in {
        None,
        LensGatewayLink.GatewayScope.PLATFORM,
        LensGatewayLink.GatewayScope.USER,
    }:
        raise ValidationError({"scope": "Data gateway scope is invalid."})
    if normalized_scope is None and is_local_platform_gateway_metadata(gateway.metadata):
        normalized_scope = LensGatewayLink.GatewayScope.PLATFORM

    link = _resolve_gateway_link_identity(
        org=org,
        gateway=gateway,
        owner_user=owner_user,
        normalized_scope=normalized_scope,
    )

    is_platform = link.scope == LensGatewayLink.GatewayScope.PLATFORM
    if (
        is_platform
        and not link.is_platform_default
        and is_local_platform_gateway_metadata(gateway.metadata)
    ):
        try:
            with transaction.atomic():
                locked_link = LensGatewayLink.objects.select_for_update().get(
                    pk=link.pk
                )
                has_platform_default = (
                    LensGatewayLink.objects.filter(
                        organization=org,
                        scope=LensGatewayLink.GatewayScope.PLATFORM,
                        is_platform_default=True,
                    )
                    .exclude(pk=locked_link.pk)
                    .exists()
                )
                if not has_platform_default:
                    locked_link.is_platform_default = True
                    locked_link.save(
                        update_fields=["is_platform_default", "updated_at"]
                    )
                link.is_platform_default = locked_link.is_platform_default
        except IntegrityError:
            # Another registration won the conditional unique constraint.
            # The link remains valid and simply is not the platform default.
            link.refresh_from_db(fields=["is_platform_default"])

    if link.sl_lensnode_uuid:
        return link
    return _provision_source_lens_lensnode(
        link=link,
        gateway=gateway,
        requested_name=name,
    )


def enable_ai_on_gateway(
    *,
    org: Organization,
    gateway: Node,
    name: str | None = None,
    owner_user=None,
    scope: str | None = None,
) -> LensGatewayLink:
    return ensure_lensnode_for_gateway(
        org=org,
        gateway=gateway,
        name=name,
        owner_user=owner_user,
        scope=scope,
    )


def build_lens_enroll_config(link: LensGatewayLink) -> dict[str, Any]:
    """LensNode credentials for gateway enrollment / sidecar install."""
    from apps.lens_bridge.deploy import (
        lens_gateway_base_path,
        lens_gateway_base_url,
        local_platform_lens_gateway_base_url,
    )
    from apps.node.services.internal.local_platform_gateway import (
        is_local_platform_gateway_metadata,
    )

    config = link.config_json or {}
    gateway = link.gateway
    lensnode_name = f"hfl-gw-{gateway.id}-{gateway.name}"[:160]
    gateway_base_url = lens_gateway_base_url()
    if is_local_platform_gateway_metadata(gateway.metadata):
        gateway_base_url = local_platform_lens_gateway_base_url()
    return {
        "lens_base_url": gateway_base_url,
        "lens_base_path": lens_gateway_base_path(),
        "lensnode_uuid": str(link.sl_lensnode_uuid) if link.sl_lensnode_uuid else None,
        "lensnode_token": config.get("lensnode_token"),
        "lensnode_name": lensnode_name,
        "workspace_root": link.resolved_workspace_root(),
    }


def provision_gateway_lens_on_register(
    *,
    org: Organization,
    gateway: Node,
    owner_user=None,
    scope: str | None = None,
) -> dict[str, Any] | None:
    """Auto-provision LensNode when a gateway registers; returns enroll config or None."""
    from apps.lens_bridge.deploy import lens_bridge_configured

    if gateway.role != NodeRole.GATEWAY or not lens_bridge_configured():
        return None
    try:
        link = ensure_lensnode_for_gateway(
            org=org,
            gateway=gateway,
            owner_user=owner_user,
            scope=scope,
        )
        return build_lens_enroll_config(link)
    except Exception:
        logger.warning(
            "gateway lens provision failed gateway_id=%s",
            gateway.id,
            exc_info=True,
        )
        return None


def record_gateway_install_status(
    *,
    org: Organization,
    gateway: Node,
    status: str,
    error_message: str = "",
    phase: str = "install",
) -> LensGatewayLink | None:
    """Update sidecar status when gateway lifecycle reports progress."""
    if gateway.role != NodeRole.GATEWAY:
        return None

    phase = str(phase or "install").strip().lower()
    status = str(status or "").strip().lower()

    link = LensGatewayLink.objects.filter(organization=org, gateway=gateway).first()
    if link is None:
        return None

    config = dict(link.config_json or {})
    config["lifecycle_phase"] = phase
    if error_message:
        config["lifecycle_error"] = error_message[:2000]
    elif status in {"success", "running"}:
        config.pop("lifecycle_error", None)

    if status == "running":
        if phase == "sidecar_upgrade":
            link.sidecar_status = LensGatewayLink.SidecarStatus.UPGRADING
        elif phase == "sidecar_uninstall":
            link.sidecar_status = LensGatewayLink.SidecarStatus.REMOVING
        config["lifecycle_status"] = "running"
    elif status == "failed":
        link.sidecar_status = LensGatewayLink.SidecarStatus.ERROR
        config["lifecycle_status"] = "failed"
        if phase == "install":
            config["install_status"] = "failed"
            if error_message:
                config["install_error"] = error_message[:2000]
    else:
        config["lifecycle_status"] = "success"
        if phase == "install":
            config["install_status"] = "success"
            config.pop("install_error", None)
            if link.sl_lensnode_uuid and link.sidecar_status == LensGatewayLink.SidecarStatus.ERROR:
                link.sidecar_status = LensGatewayLink.SidecarStatus.OFFLINE
        elif phase == "sidecar_upgrade":
            link.sidecar_status = LensGatewayLink.SidecarStatus.OFFLINE
        elif phase == "sidecar_uninstall":
            link.sidecar_status = LensGatewayLink.SidecarStatus.NOT_DEPLOYED
            config.pop("install_status", None)

    link.config_json = config
    link.save(update_fields=["sidecar_status", "config_json", "updated_at"])
    return link


def _extract_sl_lensnode_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    tasks: list[dict[str, str]] = []
    for item in data.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("task") or "").strip()
        title = str(item.get("title") or name).strip()
        if name or title:
            tasks.append({"name": name, "title": title or name})
    uuid_raw = data.get("uuid")
    return {
        "sl_name": str(data.get("name") or "").strip(),
        "sl_lensnode_uuid": str(uuid_raw).strip() if uuid_raw else "",
        "sl_status": str(data.get("status") or "").strip(),
        "sl_workspace_path": str(data.get("workspace_path") or "").strip(),
        "sl_agent_version": str(data.get("agent_version") or "").strip(),
        "sl_last_heartbeat_at": data.get("last_heartbeat_at"),
        "sl_registered_at": data.get("registered_at"),
        "sl_tasks": tasks,
    }


def _empty_sl_lensnode_snapshot() -> dict[str, Any]:
    return {
        "sl_name": "",
        "sl_lensnode_uuid": "",
        "sl_status": "",
        "sl_workspace_path": "",
        "sl_agent_version": "",
        "sl_last_heartbeat_at": None,
        "sl_registered_at": None,
        "sl_tasks": [],
    }


def sl_lensnode_snapshot_from_link(link: LensGatewayLink | None) -> dict[str, Any]:
    if link is None:
        return _empty_sl_lensnode_snapshot()
    snap = (link.config_json or {}).get("sl_lensnode_snapshot")
    if isinstance(snap, dict) and snap:
        out = _empty_sl_lensnode_snapshot()
        out.update({k: snap.get(k, out[k]) for k in out})
        if link.sl_lensnode_uuid and not out["sl_lensnode_uuid"]:
            out["sl_lensnode_uuid"] = str(link.sl_lensnode_uuid)
        if not out["sl_workspace_path"]:
            out["sl_workspace_path"] = link.resolved_workspace_root()
        return out
    out = _empty_sl_lensnode_snapshot()
    if link.sl_lensnode_uuid:
        out["sl_lensnode_uuid"] = str(link.sl_lensnode_uuid)
    out["sl_workspace_path"] = link.resolved_workspace_root()
    return out


def build_gateway_ai_payload(
    *,
    gateway: Node,
    link: LensGatewayLink | None,
    include_token: bool = False,
) -> dict[str, Any]:
    snap = sl_lensnode_snapshot_from_link(link)
    workspace = snap.get("sl_workspace_path") or (link.resolved_workspace_root() if link else "")
    display_snap = {k: v for k, v in snap.items() if k != "sl_lensnode_uuid"}
    payload: dict[str, Any] = {
        "gateway_id": gateway.id,
        "ai_enabled": bool(link and link.sl_lensnode_uuid),
        "sidecar_status": link.sidecar_status if link else LensGatewayLink.SidecarStatus.NOT_DEPLOYED,
        "workspace_root": workspace,
        "sl_lensnode_uuid": link.sl_lensnode_uuid if link and link.sl_lensnode_uuid else None,
        **display_snap,
    }
    if include_token and link is not None:
        payload["lensnode_token"] = (link.config_json or {}).get("lensnode_token")
    return payload


def apply_gateway_lensnode_snapshot(
    link: LensGatewayLink,
    data: dict[str, Any],
) -> LensGatewayLink:
    """Persist a LensNode heartbeat payload without overriding active lifecycle work."""
    preserve_lifecycle = link.sidecar_status in (
        LensGatewayLink.SidecarStatus.REMOVING,
        LensGatewayLink.SidecarStatus.UPGRADING,
    )
    update_fields: list[str] = []
    if not preserve_lifecycle:
        sl_status = str(data.get("status") or "").lower()
        if sl_status == "online":
            next_status = LensGatewayLink.SidecarStatus.ONLINE
        elif sl_status == "offline":
            next_status = LensGatewayLink.SidecarStatus.OFFLINE
        else:
            next_status = LensGatewayLink.SidecarStatus.OFFLINE
        if link.sidecar_status != next_status:
            link.sidecar_status = next_status
            update_fields.append("sidecar_status")

    config = dict(link.config_json or {})
    snapshot = _extract_sl_lensnode_snapshot(data)
    if config.get("sl_lensnode_snapshot") != snapshot:
        config["sl_lensnode_snapshot"] = snapshot
        link.config_json = config
        update_fields.append("config_json")
    if update_fields:
        link.save(update_fields=[*update_fields, "updated_at"])
    return link


def sync_gateway_lensnode_status(link: LensGatewayLink) -> LensGatewayLink:
    if not link.sl_lensnode_uuid:
        if link.sidecar_status != LensGatewayLink.SidecarStatus.NOT_DEPLOYED:
            link.sidecar_status = LensGatewayLink.SidecarStatus.NOT_DEPLOYED
            link.save(update_fields=["sidecar_status", "updated_at"])
        return link

    try:
        data = sl_client.request_json("GET", f"/api/lens/admin/lensnodes/{link.sl_lensnode_uuid}/")
    except sl_client.LensBridgeError:
        if link.sidecar_status not in (
            LensGatewayLink.SidecarStatus.REMOVING,
            LensGatewayLink.SidecarStatus.UPGRADING,
            LensGatewayLink.SidecarStatus.ERROR,
        ):
            link.sidecar_status = LensGatewayLink.SidecarStatus.ERROR
            link.save(update_fields=["sidecar_status", "updated_at"])
        return link
    return apply_gateway_lensnode_snapshot(link, data)


def _entry_is_dir(item: dict[str, Any]) -> bool:
    if item.get("is_dir") is True:
        return True
    path_type = str(item.get("path_type") or item.get("type") or "").lower()
    if path_type in {"directory", "dir", "folder"}:
        return True
    return bool(item.get("isLeaf") is False)


def _normalize_gateway_browse_entries(
    raw_entries: Any,
    *,
    workspace_root: str,
) -> list[dict[str, Any]]:
    from apps.lens_bridge.services.gateway_paths import (
        GatewayPathError,
        path_within_root,
    )

    if not isinstance(raw_entries, list):
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_entries:
        if not isinstance(item, dict):
            continue
        is_dir = _entry_is_dir(item)
        if not is_dir:
            continue
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        try:
            path = path_within_root(
                path,
                workspace_root,
                allow_root=False,
                field="entry_path",
            )
        except GatewayPathError:
            continue
        seen.add(path)
        name = str(item.get("name") or item.get("label") or "").strip() or path.rstrip("/").split("/")[-1]
        rows.append(
            {
                "name": name,
                "path": path,
                "type": "dir",
                "size_bytes": 0,
                "modified_at": str(item.get("mod_time") or item.get("modified_at") or "") or None,
                "downloadable": False,
                "has_children": True,
            }
        )
    return sorted(rows, key=lambda row: row["name"].lower())


def browse_gateway_directory(
    *,
    org: Organization,
    gateway_id: int,
    path: str = "",
    expected_scope: str | None = None,
    expected_owner_user_id: int | None = None,
    limit: int = 200,
    wait_timeout_seconds: int = 15,
) -> dict[str, Any]:
    from apps.node.services.interface import run_agent_task_sync
    from apps.lens_bridge.services.gateway_paths import (
        GatewayPathError,
        path_within_root,
    )

    gateway = require_gateway_node(org, gateway_id)
    if gateway.availability != Node.Availability.ONLINE:
        raise ValidationError({"gateway": "Data gateway must be online to browse directories."})

    link = get_gateway_link(org, gateway.id)
    if expected_scope is not None and link.scope != expected_scope:
        raise ValidationError({"gateway_id": "Data gateway scope is invalid."})
    if expected_scope is not None or expected_owner_user_id is not None:
        from apps.lens_bridge.services.gateway_execution import (
            context_for_gateway_link,
        )

        context_for_gateway_link(
            tenant_organization=org,
            gateway_link=link,
            expected_owner_user_id=expected_owner_user_id,
            require_ready=False,
        )

    normalized_root = link.resolved_workspace_root()
    try:
        browse_path = path_within_root(
            str(path or "").strip() or normalized_root,
            normalized_root,
            allow_root=True,
        )
    except GatewayPathError as exc:
        raise ValidationError(
            {"path": "Path must be under the gateway workspace root."}
        ) from exc

    outcome = run_agent_task_sync(
        organization_id=org.id,
        node_id=gateway.id,
        kind="lens.gateway.browse",
        payload={
            "path": browse_path,
            "allowed_root": normalized_root,
            "dirs_only": True,
            "include_metadata": False,
            "limit": limit,
        },
        correlation_type="lens.gateway.browse",
        correlation_id=str(gateway_id),
        wait_timeout_seconds=wait_timeout_seconds,
    )
    if outcome.timed_out:
        raise ValidationError({"detail": "Directory listing timed out."})
    if not outcome.ok:
        error = getattr(outcome.task, "last_error", "") or "Directory listing failed."
        raise ValidationError({"detail": error})

    try:
        result = outcome.result
    except (TypeError, ValueError):
        result = {}
    if not isinstance(result, dict):
        result = {}

    listed_path = str(result.get("path") or browse_path).strip() or browse_path
    try:
        listed_path = path_within_root(
            listed_path,
            normalized_root,
            allow_root=True,
            field="listed_path",
        )
    except GatewayPathError:
        listed_path = browse_path
    import posixpath

    parent_path = posixpath.dirname(listed_path.rstrip("/")) or normalized_root
    try:
        parent_path = path_within_root(
            parent_path,
            normalized_root,
            allow_root=True,
            field="parent_path",
        )
    except GatewayPathError:
        parent_path = normalized_root

    return {
        "gateway_id": gateway_id,
        "path": listed_path,
        "root_path": normalized_root,
        "parent_path": parent_path,
        "entries": _normalize_gateway_browse_entries(
            result.get("entries"),
            workspace_root=normalized_root,
        ),
        "has_more": bool(result.get("has_more")),
        "next_cursor": str(result.get("next_cursor") or ""),
    }
