"""Tenant permission contract shared by Host endpoints and Enterprise roles.

Categories organize the editor only. Every key is an independent capability;
``access_all`` changes resource scope, never the set of allowed operations.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    key: str
    name: str
    resource_name: str
    contexts: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    sensitive: bool = False

    def payload(self) -> dict:
        module, resource, action = self.key.split(".")
        return {
            "key": self.key,
            "name": self.name,
            "module": module,
            "resource": resource,
            "resource_name": self.resource_name,
            "action": action,
            "allowed_contexts": list(self.contexts),
            "dependencies": list(self.dependencies),
            "sensitive": self.sensitive,
            "description": self.name,
        }


# module, resource, display name, independently assignable actions
_RESOURCES = (
    ("configuration", "organizations", "Organization", ("view",)),
    (
        "configuration",
        "members",
        "Members",
        ("view", "manage", "assign_role", "remove"),
    ),
    ("configuration", "roles", "Roles", ("view", "manage", "delete")),
    (
        "configuration",
        "groups",
        "Groups",
        ("view", "manage", "delete", "manage_members", "manage_resources"),
    ),
    (
        "configuration",
        "resources",
        "Resource Directory",
        ("view", "access_all", "change_owner"),
    ),
    ("configuration", "subscriptions", "Subscription", ("view",)),
    (
        "protection",
        "source_hosts",
        "Source Hosts",
        ("view", "manage", "upgrade", "delete"),
    ),
    ("protection", "source_nas", "Source NAS", ("view", "manage", "delete")),
    (
        "protection",
        "proxy_hosts",
        "Proxy Hosts",
        ("view", "manage", "upgrade", "delete"),
    ),
    (
        "protection",
        "object_storage_repositories",
        "Object Storage Repositories",
        ("view", "manage", "delete"),
    ),
    (
        "protection",
        "target_nas_repositories",
        "Target NAS Repositories",
        ("view", "manage", "delete"),
    ),
    (
        "protection",
        "local_disk_repositories",
        "Local Disk Repositories",
        ("view", "manage", "delete"),
    ),
    (
        "protection",
        "backup_configurations",
        "Backup Configurations",
        ("view", "manage", "run", "cancel", "delete"),
    ),
    ("protection", "backup_policies", "Backup Policies", ("view", "manage", "delete")),
    ("protection", "file_filters", "File Filters", ("view", "manage", "delete")),
    ("protection", "tags", "Tags", ("view", "manage", "delete")),
    (
        "protection",
        "restore_plans",
        "Restore Plans",
        ("view", "manage", "run", "cancel", "delete"),
    ),
    ("protection", "backup_data", "Backup Data", ("browse", "download", "delete")),
    (
        "insights",
        "private_data_gateways",
        "Private Data Gateways",
        ("view", "manage", "upgrade", "use", "delete"),
    ),
    ("insights", "chats", "Chats", ("view", "use", "share", "delete")),
    ("insights", "ai_models", "AI Models", ("view", "manage", "delete")),
    ("insights", "reports", "Insight Reports", ("view", "manage", "delete")),
    ("insights", "usage", "Insight Usage", ("view",)),
    ("operations", "host_monitoring", "Host Monitoring", ("view",)),
    ("operations", "tasks", "Tasks", ("view",)),
    ("operations", "events", "Events", ("view",)),
    ("operations", "alerts", "Alerts", ("view", "acknowledge", "resolve")),
    ("operations", "alert_rules", "Alert Rules", ("view", "manage", "delete")),
    (
        "operations",
        "notification_channels",
        "Notification Channels",
        ("view", "manage", "test", "delete"),
    ),
    ("operations", "notification_records", "Delivery History", ("view",)),
    ("operations", "audit_logs", "Audit Logs", ("view",)),
)
_VERBS = {
    "view": "View",
    "manage": "Manage",
    "delete": "Delete",
    "upgrade": "Upgrade",
    "run": "Run",
    "cancel": "Cancel",
    "use": "Use",
    "share": "Share",
    "browse": "Browse",
    "download": "Download",
    "test": "Test",
    "acknowledge": "Acknowledge",
    "resolve": "Resolve",
}
_NAMES = {
    "configuration.members.assign_role": "Assign Organization Roles",
    "configuration.members.remove": "Remove Members",
    "configuration.groups.manage_members": "Manage Group Members",
    "configuration.groups.manage_resources": "Manage Group Resources",
    "configuration.resources.access_all": "Access All Organization Resources",
    "configuration.resources.change_owner": "Change Resource Owner",
    "protection.backup_configurations.run": "Run Backups",
    "protection.backup_configurations.cancel": "Cancel Backups",
    "protection.restore_plans.run": "Run Restores",
    "protection.restore_plans.cancel": "Cancel Restores",
    "insights.chats.use": "Use Chat",
    "insights.chats.share": "Share Q&A",
}
GROUP_CONFIGURATION_ACTIONS = frozenset(
    {
        "configuration.groups.view",
        "configuration.groups.manage_members",
        "configuration.groups.manage_resources",
        "configuration.resources.view",
    }
)
SENSITIVE_ACTIONS = frozenset(
    {
        "configuration.members.assign_role",
        "configuration.roles.manage",
        "configuration.groups.manage_members",
        "configuration.groups.manage_resources",
        "configuration.resources.access_all",
        "configuration.resources.change_owner",
        "protection.restore_plans.run",
        "protection.backup_data.download",
        "protection.backup_data.delete",
        "insights.chats.share",
        "insights.ai_models.manage",
        "insights.ai_models.delete",
        "insights.reports.delete",
    }
)


def _build_catalog() -> dict[str, Permission]:
    catalog = {}
    for module, resource, name, actions in _RESOURCES:
        for action in actions:
            key = f"{module}.{resource}.{action}"
            contexts = (
                ("organization",)
                if (
                    module == "configuration"
                    and key not in GROUP_CONFIGURATION_ACTIONS
                    or key == "insights.usage.view"
                    or resource == "ai_models"
                    or resource == "tags"
                    and action in ("manage", "delete")
                )
                else ("organization", "group")
            )
            dependencies = ()
            if action not in ("view", "browse") and "view" in actions:
                dependencies = (f"{module}.{resource}.view",)
            if resource == "backup_data" and action == "browse":
                dependencies = ("protection.backup_configurations.view",)
            if key == "protection.backup_data.download":
                dependencies = ("protection.backup_data.browse",)
            if key == "protection.backup_data.delete":
                dependencies = ("protection.backup_configurations.view",)
            if key == "configuration.groups.manage_resources":
                dependencies = (
                    "configuration.groups.view",
                    "configuration.resources.view",
                )
            if key == "configuration.resources.access_all":
                dependencies = ()
            catalog[key] = Permission(
                key,
                _NAMES.get(key, f"{_VERBS.get(action, action)} {name}"),
                name,
                contexts,
                dependencies,
                key in SENSITIVE_ACTIONS,
            )
    return catalog


PERMISSIONS = _build_catalog()
PERMISSION_KEYS = frozenset(PERMISSIONS)
ORGANIZATION_SCOPE_PERMISSION = "configuration.resources.access_all"


def normalize_permissions(values) -> frozenset[str]:
    """Expand only explicit dependencies, never wildcards or module grants."""
    result = set(values)
    pending = list(result)
    while pending:
        item = PERMISSIONS.get(pending.pop())
        for dependency in item.dependencies if item else ():
            if dependency not in result:
                result.add(dependency)
                pending.append(dependency)
    return frozenset(result)


RESOURCE_PERMISSION_PREFIXES = {
    "backup_config": "protection.backup_configurations",
    "restore_plan": "protection.restore_plans",
    "backup_policy": "protection.backup_policies",
    "file_filter_rule": "protection.file_filters",
    "source_resource": "protection.source_nas",
    "alert_policy": "operations.alert_rules",
    "notification_channel": "operations.notification_channels",
    "resource_group": "configuration.groups",
}
NODE_PERMISSION_PREFIXES = {
    "agent": "protection.source_hosts",
    "proxy": "protection.proxy_hosts",
    "gateway": "insights.private_data_gateways",
}
REPOSITORY_PERMISSION_PREFIXES = {
    "s3": "protection.object_storage_repositories",
    "nas": "protection.target_nas_repositories",
    "proxy_fs": "protection.local_disk_repositories",
}


def resource_permission_prefix(kind: str, obj=None) -> str | None:
    if kind == "node":
        return NODE_PERMISSION_PREFIXES.get(str(getattr(obj, "role", "")))
    if kind == "repository":
        return REPOSITORY_PERMISSION_PREFIXES.get(str(getattr(obj, "repo_type", "")))
    if kind == "source_resource" and obj is not None:
        source_type = str(getattr(obj, "resource_type", ""))
        if source_type == "local":
            return "protection.source_hosts"
        if source_type not in ("nas", "nfs", "cifs"):
            return None
    return RESOURCE_PERMISSION_PREFIXES.get(kind)


def resource_permission(kind: str, action: str, obj=None) -> str | None:
    prefix = resource_permission_prefix(kind, obj)
    key = f"{prefix}.{action}"
    return key if key in PERMISSIONS else None


# Narrow old *call-site* names during endpoint migration. These are NOT
# selectable grants and stored legacy permissions are migrated separately.
ORG_CALL_ALIASES = {
    "resources.view": "configuration.resources.view",
    "organization.view": "configuration.organizations.view",
    "members.view": "configuration.members.view",
    "members.manage": "configuration.members.manage",
    "roles.view": "configuration.roles.view",
    "roles.manage": "configuration.roles.manage",
    "subscription.view": "configuration.subscriptions.view",
    "resource_access.manage": "configuration.groups.manage",
    "group.view": "configuration.groups.view",
    "group.members.manage": "configuration.groups.manage_members",
    "group.resources.manage": "configuration.groups.manage_resources",
    "resources.organization_view": ORGANIZATION_SCOPE_PERMISSION,
}


def resource_action_permission(kind: str, action: str, obj=None) -> str | None:
    """Resolve endpoint intents to one resource-specific action, not a union."""
    if action in PERMISSIONS:
        return action
    if action.startswith("resource."):
        return resource_permission(kind, action.split(".", 1)[1], obj)
    if action in ("resources.view", "protection.view"):
        return resource_permission(kind, "view", obj)
    if action in (
        "resources.manage",
        "protection.manage",
        "operations.manage",
        "insight.manage",
    ):
        return resource_permission(kind, "manage", obj)
    if action == "insight.use":
        return (
            "insights.private_data_gateways.use"
            if (kind == "node" and getattr(obj, "role", "") == "gateway")
            else "insights.chats.use"
        )
    return ORG_CALL_ALIASES.get(action)
