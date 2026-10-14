"""Request-facing membership authority; Group grants never grant org authority."""

from __future__ import annotations

from typing import Any

from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.iam.models import Membership
from apps.iam.permissions_org import get_effective_role, get_membership
from apps.iam.services.membership_service import authoritative_role
from common.extension_spi import get_authz_provider


ASSIGNABLE_ROLES = {
    "owner": ("admin", "manager", "operator", "auditor"),
    "admin": ("admin", "manager", "operator", "auditor"),
    "manager": ("operator", "auditor"),
}


def membership_management_profile(request: Any) -> dict[str, Any]:
    """Describe organization-level administration, not resource Group rights."""
    actor = get_membership(request)
    role = get_effective_role(request, actor) if actor else None
    provider = get_authz_provider()
    gate = getattr(provider, "has_org_action", None)
    assignable_roles = None
    role_names = None
    if actor and provider is not None:
        resolver = getattr(provider, "assignable_org_roles", None)
        if callable(resolver):
            assignable_roles = list(resolver(request.user, actor.organization.key))
        names_resolver = getattr(provider, "organization_role_names", None)
        if callable(names_resolver):
            role_names = names_resolver(organization_id=actor.organization.pk)
    active = bool(
        actor
        and getattr(request.user, "is_active", False)
        and not getattr(request, "hfl_support_readonly", False)
    )

    def allowed(action):
        return bool(
            active
            and (provider is None or gate(request.user, actor.organization.key, action))
        )

    return {
        "can_add": allowed("configuration.members.manage")
        and (provider is None or bool(assignable_roles)),
        "can_assign_role": allowed("configuration.members.assign_role"),
        "can_manage": allowed("configuration.members.manage"),
        "can_remove": allowed("configuration.members.remove"),
        "assignable_roles": assignable_roles
        if assignable_roles is not None
        else list(ASSIGNABLE_ROLES.get(role, ())),
        "role_names": role_names or {},
        "organization_id": actor.organization.pk if actor else None,
        "actor_role": role,
    }


def membership_management_permissions(
    request: Any,
    target: Membership,
    *,
    profile: dict | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Explain row actions. The mutation path recomputes this under its lock."""
    profile = profile if profile is not None else membership_management_profile(request)
    provider = get_authz_provider()
    role = authoritative_role(target) if role is None else role
    roles = profile["assignable_roles"]
    eligible = bool(
        target.organization_id == profile["organization_id"]
        and not target.user.is_staff
        and not target.user.is_superuser
    )
    if provider is not None:
        eligible = eligible and target.user_id != request.user.id and role != "owner"
    can_activate = (
        eligible and not target.is_active and target.user.is_active and bool(role)
    )
    return {
        "can_edit_role": eligible and profile["can_assign_role"],
        "can_deactivate": eligible and profile["can_manage"] and target.is_active,
        "can_activate": can_activate and profile["can_manage"],
        "can_remove": eligible and profile["can_remove"],
        "assignable_roles": roles if eligible and profile["can_assign_role"] else [],
        "protected_reason": (
            "platform_account" if target.user.is_staff or target.user.is_superuser
            else "self" if provider is not None and target.user_id == request.user.id
            else "owner" if provider is not None and role == "owner"
            else "no_permission" if not any((profile["can_assign_role"], profile["can_manage"], profile["can_remove"]))
            else ""
        ),
    }


def assert_membership_management(
    request: Any,
    *,
    operation: str,
    target: Membership | None = None,
    role: str | None = None,
) -> None:
    """Enforce current actor, hierarchy and assignment bounds at mutation time."""
    profile = membership_management_profile(request)
    if target is None and not profile["can_add"]:
        raise PermissionDenied("Organization member management is not granted.")
    if target is not None:
        actions = membership_management_permissions(request, target, profile=profile)
        if not actions.get(operation, False):
            raise PermissionDenied(
                "This organization membership cannot be managed by you."
            )
    if role is not None and role not in profile["assignable_roles"]:
        raise PermissionDenied("This organization role cannot be assigned by you.")
    recovery_check = getattr(get_authz_provider(), "assert_membership_recovery", None)
    if target is not None and callable(recovery_check):
        recovery_check(
            organization_id=target.organization_id,
            target_user_id=target.user_id,
            operation=operation,
            role=role,
        )


def assert_eligible_member_account(user: Any) -> None:
    """Do not turn tenant membership administration into platform account CRUD."""
    if not user.is_active or user.is_staff or user.is_superuser:
        raise ValidationError(
            {"email": "An eligible active account could not be found."}
        )
