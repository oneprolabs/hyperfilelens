"""
Org-scoped DRF permissions (IAM domain).

Affiliation lives on OSS ``Membership``. Role authority:
  - Community (no AuthzProvider): active member ⇒ full power (product: single-user org).
  - Enterprise: ``AuthzProvider`` (EE ``MemberRole`` table).
Trusted installation authority is independent of affiliation and ordinary Roles.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from rest_framework import permissions
from rest_framework.request import Request

from apps.iam.constants import SUPPORT_SESSION_KEY
from apps.iam.models import Membership, Organization
from common.extension_spi import get_authz_provider
from common.platform_staff import is_installation_admin


# Compatibility value for legacy request permission gates, never a stored Role.
INSTALLATION_AUTHORITY = "__installation_admin__"


def installation_org_context(user: Any, org: Organization) -> SimpleNamespace:
    """Describe access, not affiliation; never persist this as a Membership."""
    return SimpleNamespace(
        user=user, user_id=user.pk, organization=org, organization_id=org.pk,
        authority_source="installation_admin", is_active=True,
        preferred_feature="", id=None, pk=None,
    )


def resolve_org_key(request: Request) -> str:
    return str(
        request.headers.get("X-Org-Key", "")
        or request.query_params.get("org", "")
        or ""
    ).strip()


def get_membership(request: Request) -> Membership | SimpleNamespace | None:
    """Resolve affiliation or an explicitly identified, non-persistent context."""
    user = request.user
    if not user or not user.is_authenticated or not user.is_active:
        return None
    org_key = resolve_org_key(request)
    if not org_key:
        return None
    org = Organization.objects.filter(key=org_key, is_active=True).first()
    if org is None:
        return None
    # Explicit support mode takes precedence even for a real member/Owner/root.
    if user.is_staff and getattr(request, "session", {}).get(SUPPORT_SESSION_KEY) == org_key:
        request.hfl_support_readonly = True
        return SimpleNamespace(
            user=user, user_id=user.pk,
            organization=org, organization_id=org.pk,
            role=Membership.Role.AUDITOR,
            authority_source="support_readonly",
            preferred_feature="", is_active=True, id=None, pk=None,
        )
    membership = (
        Membership.objects.select_related("organization", "user")
        .filter(user=user, organization=org, is_active=True)
        .first()
    )
    if membership is not None:
        return membership
    if is_installation_admin(user):
        return installation_org_context(user, org)
    return None


def get_effective_role(request: Request, membership=None) -> str | None:
    """Authoritative org role for permission checks."""
    membership = membership if membership is not None else get_membership(request)
    if membership is None:
        return None
    if getattr(request, "hfl_support_readonly", False):
        return Membership.Role.AUDITOR
    if is_installation_admin(request.user):
        return INSTALLATION_AUTHORITY
    org_key = getattr(
        getattr(membership, "organization", None), "key", ""
    ) or resolve_org_key(request)
    provider = get_authz_provider()
    if provider is not None:
        role = provider.get_org_role(request.user, org_key)
        return role
    # Community: affiliation implies full tenant power.
    return Membership.Role.OWNER


def is_custom_role_member(request, membership=None) -> bool:
    """Optional EE capability; never infer authority from an unknown role key."""
    membership = membership if membership is not None else get_membership(request)
    if membership is None or getattr(request, "hfl_support_readonly", False):
        return False
    provider = get_authz_provider()
    resolver = getattr(provider, "is_custom_org_role", None)
    role = get_effective_role(request, membership)
    return bool(
        role
        and callable(resolver)
        and resolver(
            organization_id=membership.organization.id,
            role=role,
        )
    )


class IsOrgMember(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        return get_membership(request) is not None and not (
            getattr(request, "hfl_support_readonly", False)
            and request.method not in permissions.SAFE_METHODS
        )


class IsOrgAuthorizedMember(IsOrgMember):
    """Active affiliation with an authoritative role (Community stays owner-like)."""

    def has_permission(self, request, view) -> bool:
        membership = get_membership(request)
        return (
            membership is not None
            and get_effective_role(request, membership) is not None
            and not (
                getattr(request, "hfl_support_readonly", False)
                and request.method not in permissions.SAFE_METHODS
            )
        )


class _RoleMixin:
    allowed_roles: tuple[str, ...] = ()
    custom_permission_action: str = ""

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        if (
            getattr(request, "hfl_support_readonly", False)
            and request.method not in permissions.SAFE_METHODS
        ):
            return False
        membership = get_membership(request)
        if membership is None:
            return False
        if (
            getattr(request, "hfl_support_readonly", False)
            and request.method not in permissions.SAFE_METHODS
        ):
            return False
        provider = get_authz_provider()
        if provider is not None and not getattr(request, "hfl_support_readonly", False):
            from apps.iam.query_authorization import request_authorization

            context = request_authorization(
                request, fresh=request.method not in permissions.SAFE_METHODS,
            )
            def resource_gate(kind, action):
                if context is not None:
                    return context.has_action(kind, action)
                gate = getattr(provider, "has_resource_action", None)
                return bool(callable(gate) and gate(
                    request.user, membership.organization.key, kind, action,
                ))
            action = getattr(view, "governance_actions", {}).get(
                getattr(view, "action", "")
            )
            if not action:
                action = self.custom_permission_action or (
                    getattr(view, "governance_read_action", "")
                    if request.method in permissions.SAFE_METHODS
                    else getattr(view, "governance_org_action", "")
                )
            if not action:
                action = request_permission(request, view)
            if not action and getattr(view, "governance_resource_type", ""):
                action = (
                    "resource.view"
                    if request.method in permissions.SAFE_METHODS
                    else "resource.manage"
                )
                return resource_gate(view.governance_resource_type, action)
            if action and action.startswith(
                ("configuration.groups.", "configuration.resources.view")
            ):
                return resource_gate("resource_group", action)
            if action and action.startswith(
                ("protection.", "insights.", "operations.")
            ):
                return resource_gate(
                    getattr(view, "governance_resource_type", "backup_config"), action,
                )
            if context is not None:
                return bool(action and context.has_org_action(action))
            return bool(
                action
                and provider.has_org_action(
                    request.user,
                    membership.organization.key,
                    action,
                )
            )
        if not self.allowed_roles:
            return True
        if not getattr(request, "hfl_support_readonly", False) and is_installation_admin(
            request.user,
        ):
            return True
        role = get_effective_role(request, membership)
        if role in self.allowed_roles:
            return True
        if not is_custom_role_member(request, membership):
            return False
        action = self.custom_permission_action
        if not action:
            if getattr(view, "governance_resource_type", ""):
                action = (
                    "resources.view"
                    if request.method in permissions.SAFE_METHODS
                    else (
                        getattr(view, "governance_manage_action", "protection.manage")
                    )
                )
            else:
                action = (
                    getattr(view, "governance_read_action", "")
                    if (request.method in permissions.SAFE_METHODS)
                    else getattr(view, "governance_org_action", "")
                )
        provider = get_authz_provider()
        return bool(
            action
            and provider
            and provider.has_org_action(
                request.user,
                membership.organization.key,
                action,
            )
        )


def request_permission(request, view) -> str | None:
    """Explicit module/API fallback for older Host views, without role bypass."""
    path = request.path.lower()
    read = request.method in permissions.SAFE_METHODS
    operation = getattr(view, "action", "")
    verb = (
        "view"
        if read
        else "delete"
        if request.method == "DELETE" or operation == "bulk_delete"
        else "manage"
    )
    if "/node-tokens" in path or "/enrollment/token" in path:
        from types import SimpleNamespace
        from apps.iam.permission_catalog import resource_permission

        role = (
            request.data.get("role", "agent")
            if not read and isinstance(request.data, dict)
            else request.query_params.get("role", "agent")
        )
        return resource_permission(
            "node", "view" if read else "manage", SimpleNamespace(role=role)
        )
    roots = (
        ("/iam/orgs", "configuration.organizations"),
        ("/iam/memberships", "configuration.members"),
        ("/subscription/roles", "configuration.roles"),
        ("/subscription/resource-groups", "configuration.groups"),
        ("/subscription/resources", "configuration.resources"),
        ("/subscription/", "configuration.subscriptions"),
        ("/protection/source-tags", "protection.tags"),
        ("/protection/tags", "protection.tags"),
        ("/source/tags", "protection.tags"),
        ("/protection/policies", "protection.backup_policies"),
        ("/protection/filters", "protection.file_filters"),
        ("/protection/snapshot", "protection.backup_data"),
        ("/protection/backup-source-snapshot", "protection.backup_data"),
        ("/protection/backup-target", "protection.backup_configurations"),
        ("/restore/snapshot-directories", "protection.backup_data"),
        ("/restore/records", "protection.restore_plans"),
        ("/restore/tasks", "protection.restore_plans"),
        ("/restore/target", "protection.restore_plans"),
        ("/alerts/records", "operations.alerts"),
        ("/alert/records", "operations.alerts"),
        ("/alerts/policies", "operations.alert_rules"),
        ("/alerts/rules", "operations.alert_rules"),
        ("/notification/channels", "operations.notification_channels"),
        ("/notification/records", "operations.notification_records"),
        ("/notification/logs", "operations.notification_records"),
        ("/audit", "operations.audit_logs"),
        ("/monitor/events", "operations.events"),
        ("/task", "operations.tasks"),
        ("/lens/copilot", "insights.chats"),
    )
    for root, prefix in roots:
        if root in path:
            if prefix == "insights.chats":
                verb = (
                    "view"
                    if read
                    else "share"
                    if "share" in path
                    else "delete"
                    if request.method == "DELETE"
                    else "use"
                )
            elif prefix == "protection.backup_data":
                verb = (
                    "download"
                    if "download" in path or "/content" in path or "/file" in path
                    else "browse"
                )
            elif prefix == "protection.backup_configurations":
                verb = "manage"
            elif prefix == "protection.restore_plans":
                verb = "view" if read else "cancel" if "cancel" in path else "run"
            elif prefix == "operations.alerts" and operation in (
                "acknowledge",
                "resolve",
            ):
                verb = operation
            return f"{prefix}.{verb}"
    return None


class IsOrgReader(_RoleMixin, permissions.BasePermission):
    """Read-only access for every supported organization role."""

    allowed_roles = (
        Membership.Role.OWNER,
        Membership.Role.ADMIN,
        Membership.Role.MANAGER,
        Membership.Role.OPERATOR,
        Membership.Role.AUDITOR,
    )

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        if request.method in permissions.SAFE_METHODS:
            return super().has_permission(request, view)
        return False


class IsOrgMemberReader(_RoleMixin, permissions.BasePermission):
    """Read organization membership for governance roles, including auditors."""

    allowed_roles = (
        Membership.Role.OWNER,
        Membership.Role.ADMIN,
        Membership.Role.MANAGER,
        Membership.Role.AUDITOR,
    )
    custom_permission_action = "members.view"

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        if request.method in permissions.SAFE_METHODS:
            return super().has_permission(request, view)
        return False


class IsOrgSubscriptionReader(IsOrgReader):
    """Organization quota visibility is never granted by a Group role."""

    custom_permission_action = "subscription.view"

    def has_permission(self, request, view) -> bool:
        if not super().has_permission(request, view):
            return False
        provider = get_authz_provider()
        if provider is None or getattr(request, "hfl_support_readonly", False):
            return True
        return provider.has_org_action(
            request.user,
            resolve_org_key(request),
            "subscription.view",
        )


class IsOrgStaffReader(_RoleMixin, permissions.BasePermission):
    """Read-only for operational/config data (excludes auditor)."""

    allowed_roles = (
        Membership.Role.OWNER,
        Membership.Role.ADMIN,
        Membership.Role.MANAGER,
        Membership.Role.OPERATOR,
    )

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        if request.method in permissions.SAFE_METHODS:
            return super().has_permission(request, view)
        return False


class IsOrgAdmin(_RoleMixin, permissions.BasePermission):
    """Organization governance: owner, administrator, or manager."""

    allowed_roles = (
        Membership.Role.OWNER,
        Membership.Role.ADMIN,
        Membership.Role.MANAGER,
    )


class IsOrgWriter(_RoleMixin, permissions.BasePermission):
    """Destructive / admin configuration: owner + admin only (incl. SAFE reads)."""

    allowed_roles = (Membership.Role.OWNER, Membership.Role.ADMIN)


class IsOrgOperator(_RoleMixin, permissions.BasePermission):
    """Backup and day-to-day operations: owner + admin + operator (incl. SAFE reads)."""

    allowed_roles = (
        Membership.Role.OWNER,
        Membership.Role.ADMIN,
        Membership.Role.OPERATOR,
    )
