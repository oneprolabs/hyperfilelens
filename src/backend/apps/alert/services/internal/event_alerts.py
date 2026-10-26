"""Evaluate EVENT-type alert policies when platform events occur."""

from __future__ import annotations

from apps.alert.constants import AlertType, EVENT_TYPES
from apps.alert.models import AlertPolicy
from apps.alert.services.internal.lifecycle import fire_alert
from apps.iam.models import Organization


def _resource_stub(resource_id: str, resource_name: str):
    from types import SimpleNamespace

    return SimpleNamespace(id=resource_id or "event", name=resource_name or "event")


def _audit_action_to_event(action: str) -> tuple[str, str] | None:
    """Map audit action strings to (event_category, event_type)."""
    normalized = (action or "").strip().lower()
    if not normalized:
        return None

    direct_map: dict[str, tuple[str, str]] = {
        "user.login": ("user", "login_success"),
        "user.login_failed": ("user", "login_failed"),
        "user.logout": ("user", "logout"),
        "user.create": ("user", "user_created"),
        "user.delete": ("user", "user_deleted"),
        "user.disable": ("user", "user_disabled"),
        "user.enable": ("user", "user_enabled"),
        "user.password_change": ("user", "password_changed"),
        "user.role_change": ("user", "user_role_changed"),
        "license.create": ("license", "license_added"),
        "license.update": ("license", "license_updated"),
        "license.expire": ("license", "license_expired"),
        "repository.create": ("repository", "repository_created"),
        "repository.update": ("repository", "repository_updated"),
        "repository.delete": ("repository", "repository_deleted"),
        "alert.policy.create": ("configuration", "alert_policy_changed"),
        "alert.policy.update": ("configuration", "alert_policy_changed"),
        "alert.policy.delete": ("configuration", "alert_policy_changed"),
        "notification.channel.create": ("configuration", "notification_channel_changed"),
        "notification.channel.update": ("configuration", "notification_channel_changed"),
        "notification.channel.delete": ("configuration", "notification_channel_changed"),
        "notification.delivery.failed": ("configuration", "notification_channel_changed"),
        "security.api_token.create": ("security", "api_token_created"),
        "security.api_token.delete": ("security", "api_token_deleted"),
        "security.permission.change": ("security", "permission_changed"),
    }
    if normalized in direct_map:
        return direct_map[normalized]

    if normalized.startswith("alert.policy."):
        return ("configuration", "alert_policy_changed")
    if normalized.startswith("notification."):
        return ("configuration", "notification_channel_changed")
    if normalized.startswith("repository."):
        return ("repository", "repository_updated")
    if "login" in normalized and "fail" in normalized:
        return ("user", "login_failed")
    if "login" in normalized:
        return ("user", "login_success")
    return None


def _event_in_catalog(category: str, event_type: str) -> bool:
    return event_type in (EVENT_TYPES.get(category) or [])


def handle_platform_event(
    *,
    organization: Organization,
    event_category: str,
    event_type: str,
    title: str,
    message: str = "",
    resource_type: str = "",
    resource_id: str = "",
    resource_name: str = "",
    metadata: dict | None = None,
) -> int:
    if not _event_in_catalog(event_category, event_type):
        return 0

    from django.db.models import Q

    from common.resource_scope import ResourceScope

    # Tenant EVENT policies for this org, plus platform-owned EVENT policies.
    policies = AlertPolicy.objects.filter(
        enabled=True,
        type=AlertType.EVENT,
    ).filter(
        Q(owner_scope=ResourceScope.ORGANIZATION, organization=organization)
        | Q(owner_scope=ResourceScope.PLATFORM, organization__isnull=True)
    )
    # Include the triggering tenant in alert_key so platform-owned policies
    # (organization_id is null) do not merge identical resource_id/global keys
    # from different organizations into one incident.
    resource_part = resource_id or resource_name or "global"
    alert_key = (
        f"{event_category}:{event_type}:org:{organization.id}:{resource_part}"
    )
    fired = 0
    for policy in policies:
        rule = policy.trigger_rule or {}
        rule_category = rule.get("event_category")
        rule_types = rule.get("event_types") or []
        if rule_category and rule_category != event_category:
            continue
        if rule_types and event_type not in rule_types:
            continue
        fire_alert(
            policy,
            resource=_resource_stub(resource_id, resource_name or title),
            title=title,
            message=message or title,
            alert_key=alert_key,
            metadata={
                **(metadata or {}),
                "event_category": event_category,
                "event_type": event_type,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "resource_name": resource_name,
                "organization_id": organization.id,
                "organization_key": organization.key,
            },
        )
        fired += 1
    return fired


def handle_audit_event(
    *,
    organization: Organization,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    resource_name: str = "",
    metadata: dict | None = None,
) -> int:
    mapped = _audit_action_to_event(action)
    if mapped is None:
        return 0
    category, event_type = mapped
    title = resource_name or action.replace(".", " ").replace("_", " ").title()
    return handle_platform_event(
        organization=organization,
        event_category=category,
        event_type=event_type,
        title=title,
        message=title,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        metadata={"audit_action": action, **(metadata or {})},
    )
