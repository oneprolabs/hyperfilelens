"""Platform Console authorization facade (infra vs commerce).

Host code calls ``has_platform_permission`` / ``authorize_platform``.
EE ``AuthzProvider.has_platform_permission`` requires an explicit platform
role row (bare ``is_staff`` is not enough). Without a provider, authenticated
``is_staff`` keeps full access (Community / empty socket).
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from common.errors import AppError
from common.extension_spi import get_authz_provider

# --- Action catalog (English keys; see EE docs/platform-authz-ops-vs-commerce.md) ---

INFRA_AI_MODELS_MANAGE = "platform.infra.ai_models.manage"
INFRA_PUBLIC_GATEWAY_MANAGE = "platform.infra.public_gateway.manage"
INFRA_PUBLIC_GATEWAY_CAPACITY_MANAGE = "platform.infra.public_gateway.capacity.manage"
INFRA_INSTANCE_LICENSE_VIEW = "platform.infra.instance_license.view"
INFRA_MONITORING_VIEW = "platform.infra.monitoring.view"

COMMERCE_ORG_QUOTA_MANAGE = "platform.commerce.org.quota.manage"
COMMERCE_ORG_PACKAGE_APPLY = "platform.commerce.org.package.apply"
COMMERCE_QUOTA_USAGE_VIEW = "platform.commerce.quota_usage.view"
COMMERCE_INSTANCE_POOL_VIEW = "platform.commerce.instance_pool.view"

ADMIN_INSTANCE_LICENSE_ACTIVATE = "platform.admin.instance_license.activate"
ADMIN_USERS_MANAGE = "platform.admin.users.manage"
ADMIN_ORGS_MANAGE = "platform.admin.orgs.manage"
ADMIN_SUPPORT_MANAGE = "platform.admin.support.manage"
ADMIN_AUDIT_VIEW = "platform.admin.audit.view"

ALL_PLATFORM_ACTIONS: tuple[str, ...] = (
    INFRA_AI_MODELS_MANAGE,
    INFRA_PUBLIC_GATEWAY_MANAGE,
    INFRA_PUBLIC_GATEWAY_CAPACITY_MANAGE,
    INFRA_INSTANCE_LICENSE_VIEW,
    INFRA_MONITORING_VIEW,
    COMMERCE_ORG_QUOTA_MANAGE,
    COMMERCE_ORG_PACKAGE_APPLY,
    COMMERCE_QUOTA_USAGE_VIEW,
    COMMERCE_INSTANCE_POOL_VIEW,
    ADMIN_INSTANCE_LICENSE_ACTIVATE,
    ADMIN_USERS_MANAGE,
    ADMIN_ORGS_MANAGE,
    ADMIN_SUPPORT_MANAGE,
    ADMIN_AUDIT_VIEW,
)

ROLE_PLATFORM_ADMIN = "platform_admin"
ROLE_INFRA_OPERATOR = "infra_operator"
ROLE_BILLING_OPERATOR = "billing_operator"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_PLATFORM_ADMIN: frozenset(ALL_PLATFORM_ACTIONS),
    ROLE_INFRA_OPERATOR: frozenset(
        {
            INFRA_AI_MODELS_MANAGE,
            INFRA_PUBLIC_GATEWAY_MANAGE,
            INFRA_PUBLIC_GATEWAY_CAPACITY_MANAGE,
            INFRA_INSTANCE_LICENSE_VIEW,
            INFRA_MONITORING_VIEW,
        }
    ),
    ROLE_BILLING_OPERATOR: frozenset(
        {
            COMMERCE_ORG_QUOTA_MANAGE,
            COMMERCE_ORG_PACKAGE_APPLY,
            COMMERCE_QUOTA_USAGE_VIEW,
            COMMERCE_INSTANCE_POOL_VIEW,
        }
    ),
}


def permissions_for_role(role: str | None) -> frozenset[str]:
    if not role:
        return frozenset()
    return ROLE_PERMISSIONS.get(str(role), frozenset())


def has_platform_permission(user: Any, action: str) -> bool:
    """Return True when ``user`` may perform platform ``action``."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    provider = get_authz_provider()
    if provider is not None:
        # Fail closed: a registered AuthzProvider must implement platform AuthZ.
        checker = getattr(provider, "has_platform_permission", None)
        if not callable(checker):
            return False
        return bool(checker(user, action))
    # No EE AuthZ: staff bootstrap (Community instance settings / empty socket).
    return bool(getattr(user, "is_staff", False))


def has_any_platform_permission(user: Any, actions: Sequence[str]) -> bool:
    return any(has_platform_permission(user, action) for action in actions if action)


def list_platform_permissions(user: Any) -> list[str]:
    """Stable sorted list of actions for deploy-profile / UI gating."""
    provider = get_authz_provider()
    if provider is not None:
        lister = getattr(provider, "list_platform_permissions", None)
        if not callable(lister):
            return []
        return sorted({str(a) for a in (lister(user) or [])})
    if getattr(user, "is_authenticated", False) and getattr(user, "is_staff", False):
        return sorted(ALL_PLATFORM_ACTIONS)
    return []


def get_platform_role(user: Any) -> str | None:
    provider = get_authz_provider()
    if provider is not None:
        getter = getattr(provider, "get_platform_role", None)
        if not callable(getter):
            return None
        role = getter(user)
        return str(role) if role else None
    # Community / no AuthzProvider: staff retain Console access.
    if getattr(user, "is_authenticated", False) and getattr(user, "is_staff", False):
        return ROLE_PLATFORM_ADMIN
    return None


def ensure_platform_role(
    user: Any, role: str = ROLE_PLATFORM_ADMIN
) -> None:
    """Assign a platform role when EE AuthZ is loaded (seed / bootstrap).

    Community (no AuthzProvider): no-op; bare ``is_staff`` remains sufficient.
    Enterprise: requires an explicit ``PlatformStaffRole`` row for Console access.
    """
    if not user or not getattr(user, "is_staff", False):
        return
    provider = get_authz_provider()
    if provider is None:
        return
    ensurer = getattr(provider, "ensure_platform_role", None)
    if not callable(ensurer):
        return
    ensurer(user, role)


def authorize_platform(user: Any, action: str) -> None:
    """Raise AppError when the user lacks ``action``."""
    if has_platform_permission(user, action):
        return
    raise AppError(
        code="AUTH.FORBIDDEN",
        status=403,
        title="Platform permission denied.",
        diagnostic=f"Missing platform permission: {action}",
        meta={"permission": action, "scope": "platform"},
    )


def authorize_platform_any(user: Any, actions: Iterable[str]) -> None:
    actions = [str(a) for a in actions if a]
    if has_any_platform_permission(user, actions):
        return
    raise AppError(
        code="AUTH.FORBIDDEN",
        status=403,
        title="Platform permission denied.",
        diagnostic=f"Missing one of platform permissions: {', '.join(actions)}",
        meta={"permissions": actions, "scope": "platform"},
    )
