"""Insights capabilities for selection metadata, not tenant administration."""

from apps.iam.permissions_org import (
    IsOrgAuthorizedMember,
    IsOrgStaffReader,
    IsOrgWriter,
    get_membership,
    resolve_org_key,
)
from common.extension_spi import get_authz_provider
from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied
from apps.iam.models import Organization
from apps.iam.query_authorization import request_authorization


class CanUseInsights(IsOrgAuthorizedMember):
    """Accept baseline or Group use grants; data operations still check each ref."""

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        provider = get_authz_provider()
        if provider is None:
            return IsOrgStaffReader().has_permission(request, view)
        if getattr(request, "hfl_support_readonly", False):
            return False
        org_key = resolve_org_key(request)
        if provider.has_org_action(request.user, org_key, "insights.chats.use"):
            return True
        gate = getattr(provider, "has_resource_action", None)
        return callable(gate) and any(
            gate(request.user, org_key, kind, "insights.chats.use")
            for kind in ("node", "backup_config")
        )


class OrganizationModelPermission(permissions.BasePermission):
    """Organization model configuration is not Chat use or Group authority."""

    def has_permission(self, request, view) -> bool:
        if get_authz_provider() is None:
            legacy = IsOrgStaffReader if request.method in permissions.SAFE_METHODS else IsOrgWriter
            return legacy().has_permission(request, view)
        membership = get_membership(request)
        if membership is None:
            return False
        if getattr(request, "hfl_support_readonly", False):
            return request.method in permissions.SAFE_METHODS
        action = (
            "view" if request.method in permissions.SAFE_METHODS
            else "delete" if request.method == "DELETE" else "manage"
        )
        context = request_authorization(request, fresh=request.method not in permissions.SAFE_METHODS)
        return bool(context and context.has_org_action(f"insights.ai_models.{action}") and
                    context.fence_queryset(Organization.objects.filter(pk=membership.organization_id)).exists())


def lock_model_authority(request, view) -> None:
    """Called inside model mutations, serialized with Role/member changes."""
    Organization.objects.select_for_update(no_key=True).get(pk=view.org.pk)
    if not OrganizationModelPermission().has_permission(request, view):
        raise PermissionDenied("Organization model configuration is not permitted.")


class KnowledgeSourcePermission(permissions.BasePermission):
    """Capability hints only; each knowledge source is separately scoped."""

    def has_permission(self, request, view) -> bool:
        if get_authz_provider() is None:
            legacy = IsOrgStaffReader if request.method in permissions.SAFE_METHODS else IsOrgWriter
            return legacy().has_permission(request, view)
        if get_membership(request) is None:
            return False
        if getattr(request, "hfl_support_readonly", False):
            return request.method in permissions.SAFE_METHODS
        context = request_authorization(request, fresh=request.method not in permissions.SAFE_METHODS)
        return bool(context and context.has_action("backup_config", knowledge_source_action(request)))


def knowledge_source_action(request) -> str:
    if request.method in permissions.SAFE_METHODS:
        return "insights.chats.view"
    return "insights.chats.delete" if request.method == "DELETE" else "insights.chats.use"
