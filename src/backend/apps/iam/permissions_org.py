"""
Org-scoped DRF permissions (IAM domain).

Affiliation lives on OSS ``Membership``. Role authority:
  - Community (no AuthzProvider): active member ⇒ full power (product: single-user org).
  - Enterprise: ``AuthzProvider`` (EE ``MemberRole`` table).
"""

from __future__ import annotations

from types import SimpleNamespace

from rest_framework import permissions
from rest_framework.request import Request

from apps.iam.constants import SUPPORT_SESSION_KEY
from apps.iam.models import Membership, Organization
from common.extension_spi import get_authz_provider


def resolve_org_key(request: Request) -> str:
    return str(
        request.headers.get("X-Org-Key", "")
        or request.query_params.get("org", "")
        or ""
    ).strip()


def get_membership(request: Request) -> Membership | None:
    user = request.user
    if not user or not user.is_authenticated:
        return None
    org_key = resolve_org_key(request)
    if not org_key:
        return None
    org = Organization.objects.filter(key=org_key, is_active=True).first()
    if org is None:
        return None
    membership = (
        Membership.objects.select_related("organization", "user")
        .filter(user=user, organization=org, is_active=True)
        .first()
    )
    if membership is not None:
        return membership
    if user.is_staff and request.session.get(SUPPORT_SESSION_KEY) == org_key:
        request.hfl_support_readonly = True
        return SimpleNamespace(
            user=user,
            organization=org,
            role=Membership.Role.AUDITOR,
            is_active=True,
            id=0,
            pk=0,
        )
    return None


def get_effective_role(request: Request, membership=None) -> str | None:
    """Authoritative org role for permission checks."""
    membership = membership if membership is not None else get_membership(request)
    if membership is None:
        return None
    if getattr(request, "hfl_support_readonly", False):
        return Membership.Role.AUDITOR
    org_key = getattr(getattr(membership, "organization", None), "key", "") or resolve_org_key(request)
    provider = get_authz_provider()
    if provider is not None:
        role = provider.get_org_role(request.user, org_key)
        return role
    # Community: affiliation implies full tenant power.
    return Membership.Role.OWNER


class IsOrgMember(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        return get_membership(request) is not None


class _RoleMixin:
    allowed_roles: tuple[str, ...] = ()

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        if getattr(request, "hfl_support_readonly", False) and request.method not in permissions.SAFE_METHODS:
            return False
        membership = get_membership(request)
        if membership is None:
            return False
        if not self.allowed_roles:
            return True
        role = get_effective_role(request, membership)
        return role in self.allowed_roles


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

    def has_permission(self, request, view) -> bool:  # type: ignore[override]
        if request.method in permissions.SAFE_METHODS:
            return super().has_permission(request, view)
        return False


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
