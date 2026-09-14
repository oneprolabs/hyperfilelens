"""Validated execution identity for private and shared Platform gateways."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from rest_framework.exceptions import ValidationError

from apps.iam.models import Organization
from apps.lens_bridge.models import (
    LensGatewayLink,
    LensKnowledgeSource,
    LensWorkspaceBinding,
)
from apps.lens_bridge.services import gateway_readiness
from apps.lens_bridge.services.gateway_ownership import (
    PRIVATE_GATEWAY_SCOPES,
    is_private_gateway,
)


@dataclass(frozen=True)
class GatewayExecutionContext:
    """Separates tenant data ownership from the Agent execution identity."""

    tenant_organization: Organization
    execution_organization: Organization
    gateway_link: LensGatewayLink

    @property
    def gateway(self):
        return self.gateway_link.gateway

    @property
    def is_platform(self) -> bool:
        return self.gateway_link.scope == LensGatewayLink.GatewayScope.PLATFORM


def workspace_identity_payload(binding: LensWorkspaceBinding) -> dict[str, object]:
    """Return the JSON-safe immutable identity verified by the Agent."""

    return {
        "workspace_root": binding.workspace_root,
        "workspace_uid": str(binding.workspace_uid),
        "tenant_organization_id": binding.organization_id,
        "gateway_link_id": binding.gateway_link_id,
        "knowledge_source_id": binding.knowledge_source_id,
        "workspace_kind": binding.workspace_kind,
    }

def context_for_gateway_link(
    *,
    tenant_organization: Organization,
    gateway_link: LensGatewayLink,
    require_ready: bool = True,
) -> GatewayExecutionContext:
    """Build a trusted execution context from an already-authorized link.

    Public request data must never provide an execution organization. The
    execution identity is derived from the link and its gateway Node.
    """

    link = (
        LensGatewayLink.objects.select_related("organization", "gateway")
        .filter(pk=gateway_link.pk)
        .first()
    )
    if link is None:
        raise ValidationError({"gateway_link_id": "Data gateway is not available."})
    if link.gateway.organization_id != link.organization_id:
        raise ValidationError({"gateway_link_id": "Data gateway ownership is inconsistent."})

    if link.scope == LensGatewayLink.GatewayScope.PLATFORM:
        from apps.lens_bridge.services.platform_lens import PLATFORM_ORG_KEY

        if link.organization.key != PLATFORM_ORG_KEY or link.owner_user_id is not None:
            raise ValidationError({"gateway_link_id": "Public Data Gateway identity is invalid."})
    elif is_private_gateway(link):
        if link.organization_id != tenant_organization.id:
            raise ValidationError({"gateway_link_id": "Private Data Gateway belongs to another organization."})
    else:
        raise ValidationError({"gateway_link_id": "Unsupported data gateway scope."})

    if require_ready:
        gateway_readiness.require_copilot_gateway(link)

    return GatewayExecutionContext(
        tenant_organization=tenant_organization,
        execution_organization=link.organization,
        gateway_link=link,
    )


def require_organization_gateway_link(
    *,
    tenant_organization: Organization,
    gateway_id: int,
    require_ready: bool = True,
    lock: bool = False,
) -> LensGatewayLink:
    """Resolve one Private Data Gateway through its organization boundary."""

    queryset = LensGatewayLink.objects.select_related("organization", "gateway")
    if lock:
        queryset = queryset.select_for_update()
    link = queryset.filter(
        organization=tenant_organization,
        gateway_id=gateway_id,
        scope__in=PRIVATE_GATEWAY_SCOPES,
        is_deleted=False,
    ).first()
    if link is None:
        raise ValidationError(
            {"gateway_id": "Private Data Gateway is not available in this organization."}
        )
    context_for_gateway_link(
        tenant_organization=tenant_organization,
        gateway_link=link,
        require_ready=require_ready,
    )
    return link


def context_for_knowledge_source(
    *,
    tenant_organization: Organization,
    knowledge_source,
    require_ready: bool = True,
):
    if knowledge_source.organization_id != tenant_organization.id:
        raise ValidationError(
            {"knowledge_source": "Knowledge source belongs to another organization."}
        )
    if knowledge_source.gateway_link_id is None:
        raise ValidationError({"gateway_link_id": "Knowledge source has no authoritative data gateway link."})
    context = context_for_gateway_link(
        tenant_organization=tenant_organization,
        gateway_link=knowledge_source.gateway_link,
        require_ready=require_ready,
    )
    if context.gateway.id != knowledge_source.gateway_id:
        raise ValidationError({"gateway_link_id": "Knowledge source data gateway binding is inconsistent."})
    return context


def create_workspace_binding(
    *,
    tenant_organization: Organization,
    knowledge_source: LensKnowledgeSource,
    require_ready: bool = True,
) -> LensWorkspaceBinding:
    """Create the immutable execution and filesystem identity for a new KS."""

    context = context_for_knowledge_source(
        tenant_organization=tenant_organization,
        knowledge_source=knowledge_source,
        require_ready=require_ready,
    )
    gateway_local = not (
        knowledge_source.backup_source_snapshot_id
        or knowledge_source.backup_snapshot_directory_id
    )
    kind = (
        LensWorkspaceBinding.WorkspaceKind.GATEWAY_LOCAL
        if gateway_local
        else LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE
    )
    root = context.gateway_link.resolved_workspace_root()
    workspace_uid = uuid.uuid4()
    # SourceLens advertises and accepts only direct children of the LensNode
    # workspace root as selectable directories. Keep the immutable UUID in the
    # directory name so shared Platform Gateways remain collision-free without
    # nesting tenant data below paths SourceLens cannot select.
    relative_path = "" if gateway_local else f"hfl-ks-{workspace_uid}"
    if gateway_local:
        capacity_accounted_bytes = 0
        capacity_accounting_status = (
            LensWorkspaceBinding.CapacityAccountingStatus.EXACT
        )
    else:
        from apps.lens_bridge.services.public_gateway_capacity import (
            workspace_capacity_accounting,
        )

        capacity_accounted_bytes, capacity_accounting_status = (
            workspace_capacity_accounting(
                organization_id=int(tenant_organization.id),
                scopes=list(knowledge_source.source_scopes_json or []),
            )
        )
    binding, created = LensWorkspaceBinding.objects.get_or_create(
        organization=tenant_organization,
        knowledge_source=knowledge_source,
        defaults={
            "gateway_link": context.gateway_link,
            "execution_organization_id": context.execution_organization.id,
            "execution_node_id": context.gateway.id,
            "workspace_kind": kind,
            "workspace_root": root,
            "workspace_uid": workspace_uid,
            "relative_path": relative_path,
            "state": LensWorkspaceBinding.State.PREPARING,
            "identity_status": (
                LensWorkspaceBinding.IdentityStatus.NOT_APPLICABLE
                if gateway_local
                else LensWorkspaceBinding.IdentityStatus.PENDING
            ),
            "capacity_accounted_bytes": capacity_accounted_bytes,
            "capacity_accounting_status": capacity_accounting_status,
        },
    )
    if not created:
        expected = (
            context.gateway_link.id,
            context.execution_organization.id,
            context.gateway.id,
            kind,
            root,
        )
        actual = (
            binding.gateway_link_id,
            binding.execution_organization_id,
            binding.execution_node_id,
            binding.workspace_kind,
            binding.workspace_root,
        )
        if actual != expected:
            raise ValidationError({"workspace": "Knowledge source workspace authorization is inconsistent."})
    return binding


def context_for_workspace_binding(
    *,
    tenant_organization: Organization,
    workspace_binding_id: int,
    require_ready: bool = True,
    allow_deleting: bool = False,
) -> tuple[GatewayExecutionContext, LensWorkspaceBinding]:
    """Resolve a trusted execution context from a persisted workspace binding.

    ``allow_deleting`` is reserved for identity-aware cleanup, which must also
    converge after prepare failed before the identity became ready. Restore
    callers retain the default READY-only checks.
    """

    binding = (
        LensWorkspaceBinding.objects.select_related(
            "knowledge_source",
            "gateway_link",
            "gateway_link__gateway",
            "gateway_link__organization",
        )
        .filter(
            pk=workspace_binding_id,
            organization=tenant_organization,
            is_deleted=False,
        )
        .first()
    )
    if binding is None:
        raise ValidationError({"workspace_binding_id": "Workspace binding is not available."})
    if binding.workspace_kind != LensWorkspaceBinding.WorkspaceKind.MANAGED_RESTORE:
        raise ValidationError({"workspace_binding_id": "Gateway-local directories cannot receive managed restores."})
    allowed_states = (
        {
            LensWorkspaceBinding.State.PREPARING,
            LensWorkspaceBinding.State.READY,
            LensWorkspaceBinding.State.DELETING,
            LensWorkspaceBinding.State.ERROR,
        }
        if allow_deleting
        else {LensWorkspaceBinding.State.READY}
    )
    if binding.state not in allowed_states:
        raise ValidationError({"workspace_binding_id": "Workspace binding is not executable."})
    if (
        not allow_deleting
        and binding.identity_status != LensWorkspaceBinding.IdentityStatus.READY
    ):
        raise ValidationError(
            {"workspace_binding_id": "Managed workspace identity is not verified."}
        )
    context = context_for_knowledge_source(
        tenant_organization=tenant_organization,
        knowledge_source=binding.knowledge_source,
        require_ready=require_ready,
    )
    expected = (
        context.gateway_link.id,
        context.execution_organization.id,
        context.gateway.id,
        context.gateway_link.resolved_workspace_root(),
    )
    actual = (
        binding.gateway_link_id,
        binding.execution_organization_id,
        binding.execution_node_id,
        binding.workspace_root,
    )
    try:
        binding.resolved_path()
    except ValueError as exc:
        raise ValidationError(
            {"workspace_binding_id": "Workspace path is not safely contained by its root."}
        ) from exc
    if actual != expected or not binding.relative_path:
        raise ValidationError({"workspace_binding_id": "Workspace execution identity is inconsistent."})
    return context, binding
