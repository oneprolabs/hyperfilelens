"""Platform Console authorization facade.

Permission keys follow ``platform.{module}.{resource}.{action}`` where ``module``
matches Admin Console sidebar sections:

* monitoring / alerting / ai_engine / governance / platform (settings)

Host code calls ``has_platform_permission`` / ``authorize_platform``.
EE ``AuthzProvider.has_platform_permission`` uses a live stored platform Role
or the trusted installation identity (bare ``is_staff`` is not enough).
Without a provider, authenticated ``is_staff`` keeps full access
(Community / empty socket).

Legacy ``platform.infra|commerce|admin.*`` keys are expanded by
``normalize_platform_permissions`` so stored Roles keep working.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from common.errors import AppError
from common.extension_spi import get_authz_provider

# --- Monitoring ----------------------------------------------------------------

MONITORING_HOST_MONITORING_VIEW = "platform.monitoring.host_monitoring.view"
MONITORING_SERVER_LOGS_VIEW = "platform.monitoring.server_logs.view"
MONITORING_TASKS_VIEW = "platform.monitoring.tasks.view"
MONITORING_TASKS_MANAGE = "platform.monitoring.tasks.manage"

# --- Alerting ------------------------------------------------------------------

ALERTING_ALERTS_VIEW = "platform.alerting.alerts.view"
ALERTING_ALERTS_ACKNOWLEDGE = "platform.alerting.alerts.acknowledge"
ALERTING_ALERTS_RESOLVE = "platform.alerting.alerts.resolve"
ALERTING_ALERT_RULES_VIEW = "platform.alerting.alert_rules.view"
ALERTING_ALERT_RULES_MANAGE = "platform.alerting.alert_rules.manage"
ALERTING_ALERT_RULES_DELETE = "platform.alerting.alert_rules.delete"
ALERTING_NOTIFICATION_CHANNELS_VIEW = "platform.alerting.notification_channels.view"
ALERTING_NOTIFICATION_CHANNELS_MANAGE = "platform.alerting.notification_channels.manage"
ALERTING_NOTIFICATION_CHANNELS_TEST = "platform.alerting.notification_channels.test"
ALERTING_NOTIFICATION_CHANNELS_DELETE = "platform.alerting.notification_channels.delete"
ALERTING_DELIVERY_HISTORY_VIEW = "platform.alerting.delivery_history.view"

# --- AI Engine -----------------------------------------------------------------

AI_ENGINE_AI_MODELS_VIEW = "platform.ai_engine.ai_models.view"
AI_ENGINE_AI_MODELS_MANAGE = "platform.ai_engine.ai_models.manage"
AI_ENGINE_AI_MODELS_DELETE = "platform.ai_engine.ai_models.delete"
AI_ENGINE_DATA_GATEWAYS_VIEW = "platform.ai_engine.data_gateways.view"
AI_ENGINE_DATA_GATEWAYS_MANAGE = "platform.ai_engine.data_gateways.manage"
AI_ENGINE_DATA_GATEWAY_CAPACITY_MANAGE = "platform.ai_engine.data_gateway_capacity.manage"
AI_ENGINE_DATA_GATEWAYS_DELETE = "platform.ai_engine.data_gateways.delete"
AI_ENGINE_KNOWLEDGE_SOURCES_VIEW = "platform.ai_engine.knowledge_sources.view"
AI_ENGINE_KNOWLEDGE_SOURCES_MANAGE = "platform.ai_engine.knowledge_sources.manage"
AI_ENGINE_KNOWLEDGE_SOURCES_DELETE = "platform.ai_engine.knowledge_sources.delete"
AI_ENGINE_ASSISTANTS_VIEW = "platform.ai_engine.assistants.view"
AI_ENGINE_ASSISTANTS_MANAGE = "platform.ai_engine.assistants.manage"
AI_ENGINE_ASSISTANTS_DELETE = "platform.ai_engine.assistants.delete"
AI_ENGINE_SKILLS_VIEW = "platform.ai_engine.skills.view"
AI_ENGINE_SKILLS_MANAGE = "platform.ai_engine.skills.manage"
AI_ENGINE_SKILLS_DELETE = "platform.ai_engine.skills.delete"
AI_ENGINE_MCP_SERVERS_VIEW = "platform.ai_engine.mcp_servers.view"
AI_ENGINE_MCP_SERVERS_MANAGE = "platform.ai_engine.mcp_servers.manage"
AI_ENGINE_MCP_SERVERS_DELETE = "platform.ai_engine.mcp_servers.delete"
AI_ENGINE_COPILOT_CHATS_VIEW = "platform.ai_engine.copilot_chats.view"
AI_ENGINE_COPILOT_CHATS_DELETE = "platform.ai_engine.copilot_chats.delete"
AI_ENGINE_AI_USAGE_VIEW = "platform.ai_engine.ai_usage.view"

# --- Governance ----------------------------------------------------------------

GOVERNANCE_ORGANIZATIONS_VIEW = "platform.governance.organizations.view"
GOVERNANCE_ORGANIZATIONS_MANAGE = "platform.governance.organizations.manage"
GOVERNANCE_USERS_VIEW = "platform.governance.users.view"
GOVERNANCE_USERS_MANAGE = "platform.governance.users.manage"
GOVERNANCE_PLATFORM_ROLES_VIEW = "platform.governance.platform_roles.view"
GOVERNANCE_PLATFORM_ROLES_MANAGE = "platform.governance.platform_roles.manage"
GOVERNANCE_PLATFORM_ROLES_DELETE = "platform.governance.platform_roles.delete"
GOVERNANCE_RESOURCES_VIEW = "platform.governance.resources.view"
GOVERNANCE_AUDIT_LOGS_VIEW = "platform.governance.audit_logs.view"
GOVERNANCE_SUBSCRIPTION_PLANS_VIEW = "platform.governance.subscription_plans.view"
GOVERNANCE_ORGANIZATION_QUOTAS_VIEW = "platform.governance.organization_quotas.view"
GOVERNANCE_ORGANIZATION_QUOTAS_MANAGE = "platform.governance.organization_quotas.manage"
GOVERNANCE_ORGANIZATION_PLANS_APPLY = "platform.governance.organization_plans.apply"
GOVERNANCE_LICENSE_VIEW = "platform.governance.license.view"
GOVERNANCE_INSTANCE_POOLS_VIEW = "platform.governance.instance_pools.view"
GOVERNANCE_LICENSE_ACTIVATE = "platform.governance.license.activate"
GOVERNANCE_CUSTOMER_SUPPORT_MANAGE = "platform.governance.customer_support.manage"
GOVERNANCE_TENANT_GOVERNANCE_VIEW = "platform.governance.tenant_governance.view"

# --- Platform (settings) -------------------------------------------------------

PLATFORM_AUTHENTICATION_VIEW = "platform.platform.authentication.view"
PLATFORM_AUTHENTICATION_MANAGE = "platform.platform.authentication.manage"
PLATFORM_EMAIL_SERVICE_VIEW = "platform.platform.email_service.view"
PLATFORM_EMAIL_SERVICE_MANAGE = "platform.platform.email_service.manage"
PLATFORM_EXTERNAL_ACCESS_VIEW = "platform.platform.external_access.view"
PLATFORM_EXTERNAL_ACCESS_MANAGE = "platform.platform.external_access.manage"
PLATFORM_STORAGE_PROVIDERS_VIEW = "platform.platform.storage_providers.view"
PLATFORM_STORAGE_PROVIDERS_MANAGE = "platform.platform.storage_providers.manage"
PLATFORM_RUNTIME_ENVIRONMENT_VIEW = "platform.platform.runtime_environment.view"
PLATFORM_RUNTIME_ENVIRONMENT_MANAGE = "platform.platform.runtime_environment.manage"
PLATFORM_RELEASE_PACKAGES_VIEW = "platform.platform.release_packages.view"
PLATFORM_RELEASE_PACKAGES_MANAGE = "platform.platform.release_packages.manage"

# --- Deprecated constant aliases (prefer the names above) ----------------------

INFRA_AI_MODELS_MANAGE = AI_ENGINE_AI_MODELS_MANAGE
INFRA_PUBLIC_GATEWAY_MANAGE = AI_ENGINE_DATA_GATEWAYS_MANAGE
INFRA_PUBLIC_GATEWAY_CAPACITY_MANAGE = AI_ENGINE_DATA_GATEWAY_CAPACITY_MANAGE
INFRA_INSTANCE_LICENSE_VIEW = GOVERNANCE_LICENSE_VIEW
INFRA_MONITORING_VIEW = MONITORING_HOST_MONITORING_VIEW
INFRA_MONITORING_MANAGE = MONITORING_TASKS_MANAGE
INFRA_RELEASE_PACKAGES_VIEW = PLATFORM_RELEASE_PACKAGES_VIEW
COMMERCE_ORG_QUOTA_MANAGE = GOVERNANCE_ORGANIZATION_QUOTAS_MANAGE
COMMERCE_ORG_PACKAGE_APPLY = GOVERNANCE_ORGANIZATION_PLANS_APPLY
COMMERCE_QUOTA_USAGE_VIEW = GOVERNANCE_SUBSCRIPTION_PLANS_VIEW
COMMERCE_INSTANCE_POOL_VIEW = GOVERNANCE_INSTANCE_POOLS_VIEW
COMMERCE_ORG_QUOTA_VIEW = GOVERNANCE_ORGANIZATION_QUOTAS_VIEW
ADMIN_INSTANCE_LICENSE_ACTIVATE = GOVERNANCE_LICENSE_ACTIVATE
ADMIN_USERS_MANAGE = GOVERNANCE_USERS_MANAGE
ADMIN_ORGS_MANAGE = GOVERNANCE_ORGANIZATIONS_MANAGE
ADMIN_ORGS_VIEW = GOVERNANCE_ORGANIZATIONS_VIEW
ADMIN_TENANT_GOVERNANCE_VIEW = GOVERNANCE_TENANT_GOVERNANCE_VIEW
ADMIN_RESOURCES_VIEW = GOVERNANCE_RESOURCES_VIEW
ADMIN_SUPPORT_MANAGE = GOVERNANCE_CUSTOMER_SUPPORT_MANAGE
ADMIN_AUDIT_VIEW = GOVERNANCE_AUDIT_LOGS_VIEW

ALL_PLATFORM_ACTIONS: tuple[str, ...] = (
    MONITORING_HOST_MONITORING_VIEW,
    MONITORING_SERVER_LOGS_VIEW,
    MONITORING_TASKS_VIEW,
    MONITORING_TASKS_MANAGE,
    ALERTING_ALERTS_VIEW,
    ALERTING_ALERTS_ACKNOWLEDGE,
    ALERTING_ALERTS_RESOLVE,
    ALERTING_ALERT_RULES_VIEW,
    ALERTING_ALERT_RULES_MANAGE,
    ALERTING_ALERT_RULES_DELETE,
    ALERTING_NOTIFICATION_CHANNELS_VIEW,
    ALERTING_NOTIFICATION_CHANNELS_MANAGE,
    ALERTING_NOTIFICATION_CHANNELS_TEST,
    ALERTING_NOTIFICATION_CHANNELS_DELETE,
    ALERTING_DELIVERY_HISTORY_VIEW,
    AI_ENGINE_AI_MODELS_VIEW,
    AI_ENGINE_AI_MODELS_MANAGE,
    AI_ENGINE_AI_MODELS_DELETE,
    AI_ENGINE_DATA_GATEWAYS_VIEW,
    AI_ENGINE_DATA_GATEWAYS_MANAGE,
    AI_ENGINE_DATA_GATEWAY_CAPACITY_MANAGE,
    AI_ENGINE_DATA_GATEWAYS_DELETE,
    AI_ENGINE_KNOWLEDGE_SOURCES_VIEW,
    AI_ENGINE_KNOWLEDGE_SOURCES_MANAGE,
    AI_ENGINE_KNOWLEDGE_SOURCES_DELETE,
    AI_ENGINE_ASSISTANTS_VIEW,
    AI_ENGINE_ASSISTANTS_MANAGE,
    AI_ENGINE_ASSISTANTS_DELETE,
    AI_ENGINE_SKILLS_VIEW,
    AI_ENGINE_SKILLS_MANAGE,
    AI_ENGINE_SKILLS_DELETE,
    AI_ENGINE_MCP_SERVERS_VIEW,
    AI_ENGINE_MCP_SERVERS_MANAGE,
    AI_ENGINE_MCP_SERVERS_DELETE,
    AI_ENGINE_COPILOT_CHATS_VIEW,
    AI_ENGINE_COPILOT_CHATS_DELETE,
    AI_ENGINE_AI_USAGE_VIEW,
    GOVERNANCE_ORGANIZATIONS_VIEW,
    GOVERNANCE_ORGANIZATIONS_MANAGE,
    GOVERNANCE_USERS_VIEW,
    GOVERNANCE_USERS_MANAGE,
    GOVERNANCE_PLATFORM_ROLES_VIEW,
    GOVERNANCE_PLATFORM_ROLES_MANAGE,
    GOVERNANCE_PLATFORM_ROLES_DELETE,
    GOVERNANCE_RESOURCES_VIEW,
    GOVERNANCE_AUDIT_LOGS_VIEW,
    GOVERNANCE_SUBSCRIPTION_PLANS_VIEW,
    GOVERNANCE_ORGANIZATION_QUOTAS_VIEW,
    GOVERNANCE_ORGANIZATION_QUOTAS_MANAGE,
    GOVERNANCE_ORGANIZATION_PLANS_APPLY,
    GOVERNANCE_LICENSE_VIEW,
    GOVERNANCE_INSTANCE_POOLS_VIEW,
    GOVERNANCE_LICENSE_ACTIVATE,
    GOVERNANCE_CUSTOMER_SUPPORT_MANAGE,
    GOVERNANCE_TENANT_GOVERNANCE_VIEW,
    PLATFORM_AUTHENTICATION_VIEW,
    PLATFORM_AUTHENTICATION_MANAGE,
    PLATFORM_EMAIL_SERVICE_VIEW,
    PLATFORM_EMAIL_SERVICE_MANAGE,
    PLATFORM_EXTERNAL_ACCESS_VIEW,
    PLATFORM_EXTERNAL_ACCESS_MANAGE,
    PLATFORM_STORAGE_PROVIDERS_VIEW,
    PLATFORM_STORAGE_PROVIDERS_MANAGE,
    PLATFORM_RUNTIME_ENVIRONMENT_VIEW,
    PLATFORM_RUNTIME_ENVIRONMENT_MANAGE,
    PLATFORM_RELEASE_PACKAGES_VIEW,
    PLATFORM_RELEASE_PACKAGES_MANAGE,
)

_MONITORING_VIEWS = (
    MONITORING_HOST_MONITORING_VIEW,
    MONITORING_SERVER_LOGS_VIEW,
    MONITORING_TASKS_VIEW,
)
_ALERTING_VIEWS = (
    ALERTING_ALERTS_VIEW,
    ALERTING_ALERT_RULES_VIEW,
    ALERTING_NOTIFICATION_CHANNELS_VIEW,
    ALERTING_DELIVERY_HISTORY_VIEW,
)
_ALERTING_MANAGE = (
    ALERTING_ALERTS_ACKNOWLEDGE,
    ALERTING_ALERTS_RESOLVE,
    ALERTING_ALERT_RULES_MANAGE,
    ALERTING_ALERT_RULES_DELETE,
    ALERTING_NOTIFICATION_CHANNELS_MANAGE,
    ALERTING_NOTIFICATION_CHANNELS_TEST,
    ALERTING_NOTIFICATION_CHANNELS_DELETE,
)
_PLATFORM_SETTINGS = (
    PLATFORM_AUTHENTICATION_VIEW,
    PLATFORM_AUTHENTICATION_MANAGE,
    PLATFORM_EMAIL_SERVICE_VIEW,
    PLATFORM_EMAIL_SERVICE_MANAGE,
    PLATFORM_EXTERNAL_ACCESS_VIEW,
    PLATFORM_EXTERNAL_ACCESS_MANAGE,
    PLATFORM_STORAGE_PROVIDERS_VIEW,
    PLATFORM_STORAGE_PROVIDERS_MANAGE,
    PLATFORM_RUNTIME_ENVIRONMENT_VIEW,
    PLATFORM_RUNTIME_ENVIRONMENT_MANAGE,
    PLATFORM_RELEASE_PACKAGES_VIEW,
    PLATFORM_RELEASE_PACKAGES_MANAGE,
)

# Old stored Role keys → canonical keys granted on read/normalize.
LEGACY_PLATFORM_PERMISSION_EXPANSION: dict[str, frozenset[str]] = {
    "platform.infra.monitoring.view": frozenset(_MONITORING_VIEWS + _ALERTING_VIEWS),
    "platform.infra.monitoring.manage": frozenset(
        _MONITORING_VIEWS
        + (MONITORING_TASKS_MANAGE,)
        + _ALERTING_VIEWS
        + _ALERTING_MANAGE
    ),
    "platform.infra.ai_models.manage": frozenset(
        {
            AI_ENGINE_AI_MODELS_VIEW,
            AI_ENGINE_AI_MODELS_MANAGE,
            AI_ENGINE_AI_MODELS_DELETE,
            AI_ENGINE_KNOWLEDGE_SOURCES_VIEW,
            AI_ENGINE_KNOWLEDGE_SOURCES_MANAGE,
            AI_ENGINE_KNOWLEDGE_SOURCES_DELETE,
            AI_ENGINE_ASSISTANTS_VIEW,
            AI_ENGINE_ASSISTANTS_MANAGE,
            AI_ENGINE_ASSISTANTS_DELETE,
            AI_ENGINE_SKILLS_VIEW,
            AI_ENGINE_SKILLS_MANAGE,
            AI_ENGINE_SKILLS_DELETE,
            AI_ENGINE_MCP_SERVERS_VIEW,
            AI_ENGINE_MCP_SERVERS_MANAGE,
            AI_ENGINE_MCP_SERVERS_DELETE,
            AI_ENGINE_COPILOT_CHATS_VIEW,
            AI_ENGINE_COPILOT_CHATS_DELETE,
            AI_ENGINE_AI_USAGE_VIEW,
        }
    ),
    "platform.infra.public_gateway.manage": frozenset(
        {
            AI_ENGINE_DATA_GATEWAYS_VIEW,
            AI_ENGINE_DATA_GATEWAYS_MANAGE,
            AI_ENGINE_DATA_GATEWAYS_DELETE,
        }
    ),
    "platform.infra.public_gateway.capacity.manage": frozenset(
        {AI_ENGINE_DATA_GATEWAY_CAPACITY_MANAGE, AI_ENGINE_DATA_GATEWAYS_VIEW}
    ),
    "platform.infra.instance_license.view": frozenset({GOVERNANCE_LICENSE_VIEW}),
    "platform.infra.release_packages.view": frozenset(
        {PLATFORM_RELEASE_PACKAGES_VIEW, PLATFORM_RELEASE_PACKAGES_MANAGE}
    ),
    "platform.commerce.org.quota.manage": frozenset(
        {GOVERNANCE_ORGANIZATION_QUOTAS_VIEW, GOVERNANCE_ORGANIZATION_QUOTAS_MANAGE}
    ),
    "platform.commerce.org.quota.view": frozenset({GOVERNANCE_ORGANIZATION_QUOTAS_VIEW}),
    "platform.commerce.org.package.apply": frozenset({GOVERNANCE_ORGANIZATION_PLANS_APPLY}),
    "platform.commerce.quota_usage.view": frozenset({GOVERNANCE_SUBSCRIPTION_PLANS_VIEW}),
    "platform.commerce.instance_pool.view": frozenset({GOVERNANCE_INSTANCE_POOLS_VIEW}),
    "platform.admin.instance_license.activate": frozenset({GOVERNANCE_LICENSE_ACTIVATE}),
    "platform.admin.users.manage": frozenset(
        {
            GOVERNANCE_USERS_VIEW,
            GOVERNANCE_USERS_MANAGE,
            GOVERNANCE_PLATFORM_ROLES_VIEW,
            GOVERNANCE_PLATFORM_ROLES_MANAGE,
            GOVERNANCE_PLATFORM_ROLES_DELETE,
            *_PLATFORM_SETTINGS,
        }
    ),
    "platform.admin.orgs.manage": frozenset(
        {GOVERNANCE_ORGANIZATIONS_VIEW, GOVERNANCE_ORGANIZATIONS_MANAGE}
    ),
    "platform.admin.orgs.view": frozenset({GOVERNANCE_ORGANIZATIONS_VIEW}),
    "platform.admin.tenant_governance.view": frozenset({GOVERNANCE_TENANT_GOVERNANCE_VIEW}),
    "platform.admin.resources.view": frozenset({GOVERNANCE_RESOURCES_VIEW}),
    "platform.admin.support.manage": frozenset({GOVERNANCE_CUSTOMER_SUPPORT_MANAGE}),
    "platform.admin.audit.view": frozenset({GOVERNANCE_AUDIT_LOGS_VIEW}),
}

PLATFORM_PERMISSION_DEPENDENCIES = {
    GOVERNANCE_LICENSE_ACTIVATE: (GOVERNANCE_LICENSE_VIEW,),
    MONITORING_TASKS_MANAGE: (MONITORING_TASKS_VIEW,),
    ALERTING_ALERT_RULES_MANAGE: (ALERTING_ALERT_RULES_VIEW,),
    ALERTING_ALERT_RULES_DELETE: (ALERTING_ALERT_RULES_VIEW,),
    ALERTING_ALERTS_ACKNOWLEDGE: (ALERTING_ALERTS_VIEW,),
    ALERTING_ALERTS_RESOLVE: (ALERTING_ALERTS_VIEW,),
    ALERTING_NOTIFICATION_CHANNELS_MANAGE: (ALERTING_NOTIFICATION_CHANNELS_VIEW,),
    ALERTING_NOTIFICATION_CHANNELS_TEST: (ALERTING_NOTIFICATION_CHANNELS_VIEW,),
    ALERTING_NOTIFICATION_CHANNELS_DELETE: (ALERTING_NOTIFICATION_CHANNELS_VIEW,),
    AI_ENGINE_AI_MODELS_MANAGE: (AI_ENGINE_AI_MODELS_VIEW,),
    AI_ENGINE_AI_MODELS_DELETE: (AI_ENGINE_AI_MODELS_VIEW,),
    AI_ENGINE_DATA_GATEWAYS_MANAGE: (AI_ENGINE_DATA_GATEWAYS_VIEW,),
    AI_ENGINE_DATA_GATEWAYS_DELETE: (AI_ENGINE_DATA_GATEWAYS_VIEW,),
    AI_ENGINE_DATA_GATEWAY_CAPACITY_MANAGE: (AI_ENGINE_DATA_GATEWAYS_VIEW,),
    AI_ENGINE_KNOWLEDGE_SOURCES_MANAGE: (AI_ENGINE_KNOWLEDGE_SOURCES_VIEW,),
    AI_ENGINE_KNOWLEDGE_SOURCES_DELETE: (AI_ENGINE_KNOWLEDGE_SOURCES_VIEW,),
    AI_ENGINE_ASSISTANTS_MANAGE: (AI_ENGINE_ASSISTANTS_VIEW,),
    AI_ENGINE_ASSISTANTS_DELETE: (AI_ENGINE_ASSISTANTS_VIEW,),
    AI_ENGINE_SKILLS_MANAGE: (AI_ENGINE_SKILLS_VIEW,),
    AI_ENGINE_SKILLS_DELETE: (AI_ENGINE_SKILLS_VIEW,),
    AI_ENGINE_MCP_SERVERS_MANAGE: (AI_ENGINE_MCP_SERVERS_VIEW,),
    AI_ENGINE_MCP_SERVERS_DELETE: (AI_ENGINE_MCP_SERVERS_VIEW,),
    AI_ENGINE_COPILOT_CHATS_DELETE: (AI_ENGINE_COPILOT_CHATS_VIEW,),
    GOVERNANCE_ORGANIZATIONS_MANAGE: (
        GOVERNANCE_ORGANIZATIONS_VIEW,
        GOVERNANCE_TENANT_GOVERNANCE_VIEW,
        GOVERNANCE_RESOURCES_VIEW,
    ),
    GOVERNANCE_TENANT_GOVERNANCE_VIEW: (GOVERNANCE_ORGANIZATIONS_VIEW,),
    GOVERNANCE_RESOURCES_VIEW: (GOVERNANCE_ORGANIZATIONS_VIEW,),
    GOVERNANCE_CUSTOMER_SUPPORT_MANAGE: (GOVERNANCE_ORGANIZATIONS_VIEW,),
    GOVERNANCE_USERS_MANAGE: (GOVERNANCE_USERS_VIEW,),
    GOVERNANCE_PLATFORM_ROLES_MANAGE: (GOVERNANCE_PLATFORM_ROLES_VIEW,),
    GOVERNANCE_PLATFORM_ROLES_DELETE: (GOVERNANCE_PLATFORM_ROLES_VIEW,),
    GOVERNANCE_ORGANIZATION_QUOTAS_MANAGE: (GOVERNANCE_ORGANIZATION_QUOTAS_VIEW,),
    GOVERNANCE_ORGANIZATION_PLANS_APPLY: (GOVERNANCE_ORGANIZATION_QUOTAS_VIEW,),
    GOVERNANCE_SUBSCRIPTION_PLANS_VIEW: (GOVERNANCE_ORGANIZATION_QUOTAS_VIEW,),
    GOVERNANCE_ORGANIZATION_QUOTAS_VIEW: (GOVERNANCE_ORGANIZATIONS_VIEW,),
    PLATFORM_AUTHENTICATION_MANAGE: (PLATFORM_AUTHENTICATION_VIEW,),
    PLATFORM_EMAIL_SERVICE_MANAGE: (PLATFORM_EMAIL_SERVICE_VIEW,),
    PLATFORM_EXTERNAL_ACCESS_MANAGE: (PLATFORM_EXTERNAL_ACCESS_VIEW,),
    PLATFORM_STORAGE_PROVIDERS_MANAGE: (PLATFORM_STORAGE_PROVIDERS_VIEW,),
    PLATFORM_RUNTIME_ENVIRONMENT_MANAGE: (PLATFORM_RUNTIME_ENVIRONMENT_VIEW,),
    PLATFORM_RELEASE_PACKAGES_MANAGE: (PLATFORM_RELEASE_PACKAGES_VIEW,),
}


def _expand_raw_permissions(values: Iterable[str]) -> set[str]:
    permissions: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        if value in ALL_PLATFORM_ACTIONS:
            permissions.add(value)
            continue
        expanded = LEGACY_PLATFORM_PERMISSION_EXPANSION.get(value)
        if expanded:
            permissions |= set(expanded)
    return permissions


def normalize_platform_permissions(values: Iterable[str]) -> frozenset[str]:
    """Expand legacy keys and read dependencies without inferring new writes."""
    permissions = _expand_raw_permissions(values) & set(ALL_PLATFORM_ACTIONS)
    pending = list(permissions)
    while pending:
        for dependency in PLATFORM_PERMISSION_DEPENDENCIES.get(pending.pop(), ()):
            if dependency not in permissions:
                permissions.add(dependency)
                pending.append(dependency)
    return frozenset(permissions)


ROLE_PLATFORM_ADMIN = "platform_admin"
ROLE_INFRA_OPERATOR = "infra_operator"
ROLE_TENANT_ADMIN = "tenant_admin"
ROLE_BILLING_OPERATOR = "billing_operator"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_PLATFORM_ADMIN: frozenset(ALL_PLATFORM_ACTIONS),
    ROLE_INFRA_OPERATOR: frozenset(
        {
            *_MONITORING_VIEWS,
            MONITORING_TASKS_MANAGE,
            *_ALERTING_VIEWS,
            *_ALERTING_MANAGE,
            AI_ENGINE_AI_MODELS_VIEW,
            AI_ENGINE_AI_MODELS_MANAGE,
            AI_ENGINE_AI_MODELS_DELETE,
            AI_ENGINE_DATA_GATEWAYS_VIEW,
            AI_ENGINE_DATA_GATEWAYS_MANAGE,
            AI_ENGINE_DATA_GATEWAY_CAPACITY_MANAGE,
            AI_ENGINE_DATA_GATEWAYS_DELETE,
            AI_ENGINE_KNOWLEDGE_SOURCES_VIEW,
            AI_ENGINE_KNOWLEDGE_SOURCES_MANAGE,
            AI_ENGINE_KNOWLEDGE_SOURCES_DELETE,
            AI_ENGINE_ASSISTANTS_VIEW,
            AI_ENGINE_ASSISTANTS_MANAGE,
            AI_ENGINE_ASSISTANTS_DELETE,
            AI_ENGINE_SKILLS_VIEW,
            AI_ENGINE_SKILLS_MANAGE,
            AI_ENGINE_SKILLS_DELETE,
            AI_ENGINE_MCP_SERVERS_VIEW,
            AI_ENGINE_MCP_SERVERS_MANAGE,
            AI_ENGINE_MCP_SERVERS_DELETE,
            AI_ENGINE_COPILOT_CHATS_VIEW,
            AI_ENGINE_COPILOT_CHATS_DELETE,
            AI_ENGINE_AI_USAGE_VIEW,
            GOVERNANCE_LICENSE_VIEW,
            PLATFORM_RELEASE_PACKAGES_VIEW,
            PLATFORM_RELEASE_PACKAGES_MANAGE,
        }
    ),
    ROLE_TENANT_ADMIN: frozenset(
        {
            GOVERNANCE_ORGANIZATIONS_VIEW,
            GOVERNANCE_ORGANIZATIONS_MANAGE,
            GOVERNANCE_USERS_VIEW,
            GOVERNANCE_USERS_MANAGE,
            GOVERNANCE_PLATFORM_ROLES_VIEW,
            GOVERNANCE_PLATFORM_ROLES_MANAGE,
            GOVERNANCE_PLATFORM_ROLES_DELETE,
            GOVERNANCE_RESOURCES_VIEW,
            GOVERNANCE_AUDIT_LOGS_VIEW,
            GOVERNANCE_CUSTOMER_SUPPORT_MANAGE,
            GOVERNANCE_TENANT_GOVERNANCE_VIEW,
            GOVERNANCE_LICENSE_VIEW,
        }
    ),
    ROLE_BILLING_OPERATOR: frozenset(
        {
            GOVERNANCE_ORGANIZATION_QUOTAS_VIEW,
            GOVERNANCE_ORGANIZATION_QUOTAS_MANAGE,
            GOVERNANCE_ORGANIZATION_PLANS_APPLY,
            GOVERNANCE_SUBSCRIPTION_PLANS_VIEW,
            GOVERNANCE_INSTANCE_POOLS_VIEW,
            GOVERNANCE_LICENSE_VIEW,
        }
    ),
}
ROLE_PERMISSIONS = {
    key: normalize_platform_permissions(values) for key, values in ROLE_PERMISSIONS.items()
}


def permissions_for_role(role: str | None) -> frozenset[str]:
    if not role:
        return frozenset()
    return normalize_platform_permissions(ROLE_PERMISSIONS.get(str(role), frozenset()))


def has_platform_permission(user: Any, action: str) -> bool:
    """Return True when ``user`` may perform platform ``action``."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    # Normalize legacy aliases at the check site, but do not expand a
    # canonical action's read dependencies. Dependency expansion is for
    # stored Role permissions; treating a dependency as an alternative
    # candidate here would let ``*.view`` satisfy ``*.manage``.
    candidates = (
        LEGACY_PLATFORM_PERMISSION_EXPANSION.get(action)
        or ({action} if action in ALL_PLATFORM_ACTIONS else {action})
    )
    provider = get_authz_provider()
    if provider is not None:
        checker = getattr(provider, "has_platform_permission", None)
        if not callable(checker):
            return False
        return any(bool(checker(user, candidate)) for candidate in candidates)
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
        return sorted(normalize_platform_permissions(str(a) for a in (lister(user) or [])))
    if getattr(user, "is_authenticated", False) and getattr(user, "is_staff", False):
        return sorted(ALL_PLATFORM_ACTIONS)
    return []


def get_platform_role(user: Any) -> str | None:
    """Display/landing metadata only; never derive authority from this key."""
    provider = get_authz_provider()
    if provider is not None:
        getter = getattr(provider, "get_platform_role", None)
        if not callable(getter):
            return None
        role = getter(user)
        return str(role) if role else None
    if getattr(user, "is_authenticated", False) and getattr(user, "is_staff", False):
        return ROLE_PLATFORM_ADMIN
    return None


def ensure_platform_role(
    user: Any, role: str = ROLE_PLATFORM_ADMIN
) -> None:
    """Assign a platform role when EE AuthZ is loaded (seed / bootstrap)."""
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
