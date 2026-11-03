"""Product-provided Alert Rule catalogs and explicit restore operations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from django.db import transaction

from apps.alert.constants import AlertSeverity, AlertType, PolicyScope, ResourceType
from apps.alert.models import AlertPolicy
from common.resource_scope import ResourceScope


def _task_rule(
    *,
    key: str,
    name: str,
    description: str,
    task_type: str,
    event_type: str,
) -> dict[str, Any]:
    return {
        "default_key": key,
        "name": name,
        "description": description,
        "type": AlertType.TASK,
        "severity": AlertSeverity.CRITICAL,
        "enabled": True,
        "resource_type": ResourceType.TASK,
        "scope": PolicyScope.ALL,
        "resource_ids": [],
        "trigger_rule": {
            "task_type": task_type,
            "event_type": event_type,
            "repeat_interval_seconds": 3600,
        },
        "recovery_rule": {"enabled": False, "notify_on_firing": True},
    }


ORGANIZATION_DEFAULT_ALERT_RULES: tuple[dict[str, Any], ...] = (
    _task_rule(
        key="backup.task_failed",
        name="Backup Task Failed",
        description="Alert when a backup task fails.",
        task_type="backup",
        event_type="task_failed",
    ),
    _task_rule(
        key="backup.task_timeout",
        name="Backup Task Timed Out",
        description="Alert when a backup task times out.",
        task_type="backup",
        event_type="task_timeout",
    ),
    _task_rule(
        key="restore.task_failed",
        name="Restore Task Failed",
        description="Alert when a restore task fails.",
        task_type="restore",
        event_type="task_failed",
    ),
    _task_rule(
        key="restore.task_timeout",
        name="Restore Task Timed Out",
        description="Alert when a restore task times out.",
        task_type="restore",
        event_type="task_timeout",
    ),
    {
        "default_key": "node.agent_heartbeat",
        "name": "Source Host Unavailable",
        "description": "Alert when a Source Host stops reporting heartbeats.",
        "type": AlertType.HEALTH,
        "severity": AlertSeverity.CRITICAL,
        "enabled": True,
        "resource_type": ResourceType.AGENT_PROXY,
        "scope": PolicyScope.ALL,
        "resource_ids": [],
        "trigger_rule": {
            "check_type": "heartbeat",
            "timeout_seconds": 300,
            "duration_seconds": 0,
            "evaluation_interval_seconds": 60,
        },
        "recovery_rule": {"enabled": True, "notify_on_firing": True, "notify_on_resolved": True},
    },
    {
        "default_key": "node.proxy_heartbeat",
        "name": "Proxy Host Unavailable",
        "description": "Alert when a Proxy Host stops reporting heartbeats.",
        "type": AlertType.HEALTH,
        "severity": AlertSeverity.CRITICAL,
        "enabled": True,
        "resource_type": ResourceType.SYNC_PROXY,
        "scope": PolicyScope.ALL,
        "resource_ids": [],
        "trigger_rule": {
            "check_type": "heartbeat",
            "timeout_seconds": 300,
            "duration_seconds": 0,
            "evaluation_interval_seconds": 60,
        },
        "recovery_rule": {"enabled": True, "notify_on_firing": True, "notify_on_resolved": True},
    },
    {
        "default_key": "node.gateway_heartbeat",
        "name": "Data Gateway Unavailable",
        "description": "Alert when a Data Gateway stops reporting heartbeats.",
        "type": AlertType.HEALTH,
        "severity": AlertSeverity.CRITICAL,
        "enabled": True,
        "resource_type": ResourceType.GATEWAY,
        "scope": PolicyScope.ALL,
        "resource_ids": [],
        "trigger_rule": {
            "check_type": "heartbeat",
            "timeout_seconds": 300,
            "duration_seconds": 0,
            "evaluation_interval_seconds": 60,
        },
        "recovery_rule": {"enabled": True, "notify_on_firing": True, "notify_on_resolved": True},
    },
)


PLATFORM_DEFAULT_ALERT_RULES: tuple[dict[str, Any], ...] = (
    {
        "default_key": "platform.cpu_high",
        "name": "Control Plane CPU Usage High",
        "description": "Alert when Control Plane CPU usage stays high.",
        "type": AlertType.METRIC,
        "severity": AlertSeverity.WARNING,
        "enabled": True,
        "resource_type": ResourceType.SYSTEM,
        "scope": PolicyScope.ALL,
        "resource_ids": [],
        "trigger_rule": {
            "metric_key": "cpu_usage",
            "operator": ">=",
            "threshold": 90,
            "duration_seconds": 600,
            "evaluation_interval_seconds": 60,
        },
        "recovery_rule": {"enabled": True, "operator": "<", "threshold": 85, "duration_seconds": 300},
    },
    {
        "default_key": "platform.memory_high",
        "name": "Control Plane Memory Usage High",
        "description": "Alert when Control Plane memory usage stays high.",
        "type": AlertType.METRIC,
        "severity": AlertSeverity.CRITICAL,
        "enabled": True,
        "resource_type": ResourceType.SYSTEM,
        "scope": PolicyScope.ALL,
        "resource_ids": [],
        "trigger_rule": {
            "metric_key": "memory_usage",
            "operator": ">=",
            "threshold": 90,
            "duration_seconds": 600,
            "evaluation_interval_seconds": 60,
        },
        "recovery_rule": {"enabled": True, "operator": "<", "threshold": 85, "duration_seconds": 300},
    },
    {
        "default_key": "platform.disk_high",
        "name": "Control Plane Disk Usage High",
        "description": "Alert when Control Plane disk usage stays high.",
        "type": AlertType.METRIC,
        "severity": AlertSeverity.CRITICAL,
        "enabled": True,
        "resource_type": ResourceType.SYSTEM,
        "scope": PolicyScope.ALL,
        "resource_ids": [],
        "trigger_rule": {
            "metric_key": "disk_usage",
            "operator": ">=",
            "threshold": 90,
            "duration_seconds": 600,
            "evaluation_interval_seconds": 60,
        },
        "recovery_rule": {"enabled": True, "operator": "<", "threshold": 85, "duration_seconds": 300},
    },
)


def default_catalog(*, owner_scope: str) -> tuple[dict[str, Any], ...]:
    if owner_scope == ResourceScope.PLATFORM:
        return PLATFORM_DEFAULT_ALERT_RULES
    return ORGANIZATION_DEFAULT_ALERT_RULES


def _definition_matches(policy: AlertPolicy, definition: dict[str, Any]) -> bool:
    for field in (
        "name",
        "description",
        "type",
        "severity",
        "enabled",
        "resource_type",
        "scope",
        "resource_ids",
        "trigger_rule",
        "recovery_rule",
    ):
        if getattr(policy, field) != definition[field]:
            return False
    return True


def _get_or_create_policy(
    definition: dict[str, Any],
    *,
    organization=None,
    owner_scope: str,
    created_by: int | None = None,
) -> tuple[AlertPolicy, bool]:
    """Create a default safely when two initializers race."""
    values = deepcopy(definition)
    values.pop("default_key", None)
    return AlertPolicy.objects.get_or_create(
        **_default_policy_lookup(
            owner_scope=owner_scope,
            organization=organization,
            default_key=definition["default_key"],
        ),
        defaults={
            **values,
            "created_by": created_by,
            "notification_channel_ids": [],
        },
    )


def _default_policy_lookup(*, owner_scope: str, organization, default_key: str) -> dict[str, Any]:
    lookup: dict[str, Any] = {
        "owner_scope": owner_scope,
        "default_key": default_key,
    }
    if organization is not None:
        lookup["organization"] = organization
    else:
        lookup["organization__isnull"] = True
    return lookup


def _catalog_size(*, owner_scope: str, default_keys: set[str] | None) -> int:
    catalog = default_catalog(owner_scope=owner_scope)
    if default_keys is None:
        return len(catalog)
    return len([item for item in catalog if item["default_key"] in default_keys])


def preview_restore_default_alert_rules(
    *,
    owner_scope: str,
    organization=None,
    default_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Return the create/restore actions restore_default_alert_rules would perform."""
    if owner_scope == ResourceScope.ORGANIZATION and organization is None:
        raise ValueError("organization is required for organization defaults")
    if owner_scope == ResourceScope.PLATFORM and organization is not None:
        raise ValueError("platform defaults cannot belong to an organization")

    items: list[dict[str, str]] = []
    unchanged = 0
    for definition in default_catalog(owner_scope=owner_scope):
        if default_keys is not None and definition["default_key"] not in default_keys:
            continue
        policy = AlertPolicy.objects.filter(
            **_default_policy_lookup(
                owner_scope=owner_scope,
                organization=organization,
                default_key=definition["default_key"],
            )
        ).first()
        if policy is None:
            items.append(
                {
                    "default_key": definition["default_key"],
                    "name": definition["name"],
                    "action": "create",
                }
            )
            continue
        if _definition_matches(policy, definition):
            unchanged += 1
            continue
        items.append(
            {
                "default_key": definition["default_key"],
                "name": definition["name"],
                "action": "restore",
            }
        )
    created = sum(1 for item in items if item["action"] == "create")
    restored = sum(1 for item in items if item["action"] == "restore")
    return {
        "items": items,
        "created": created,
        "restored": restored,
        "unchanged": unchanged,
        "total": _catalog_size(owner_scope=owner_scope, default_keys=default_keys),
    }


@transaction.atomic
def restore_default_alert_rules(
    *,
    owner_scope: str,
    organization=None,
    created_by: int | None = None,
    default_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Create missing defaults or restore their definition, preserving routing."""
    if owner_scope == ResourceScope.ORGANIZATION and organization is None:
        raise ValueError("organization is required for organization defaults")
    if owner_scope == ResourceScope.PLATFORM and organization is not None:
        raise ValueError("platform defaults cannot belong to an organization")

    restored = 0
    created = 0
    unchanged = 0
    for definition in default_catalog(owner_scope=owner_scope):
        if default_keys is not None and definition["default_key"] not in default_keys:
            continue
        policy = AlertPolicy.objects.select_for_update().filter(
            **_default_policy_lookup(
                owner_scope=owner_scope,
                organization=organization,
                default_key=definition["default_key"],
            )
        ).first()
        if policy is None:
            _, was_created = _get_or_create_policy(
                definition,
                organization=organization,
                owner_scope=owner_scope,
                created_by=created_by,
            )
            created += int(was_created)
            unchanged += int(not was_created)
            continue
        if _definition_matches(policy, definition):
            unchanged += 1
            continue
        routing = list(policy.notification_channel_ids or [])
        values = deepcopy(definition)
        values.pop("default_key", None)
        for field, value in values.items():
            setattr(policy, field, value)
        # Notification routing is a user preference, not part of the baseline.
        policy.notification_channel_ids = routing
        policy.save(
            update_fields=[
                *values.keys(),
                "notification_channel_ids",
                "updated_at",
            ]
        )
        restored += 1
    return {
        "created": created,
        "restored": restored,
        "unchanged": unchanged,
        "total": _catalog_size(owner_scope=owner_scope, default_keys=default_keys),
    }


def ensure_default_organization_alert_rules(organization) -> dict[str, Any]:
    """Seed defaults only for a newly created organization."""
    return create_missing_default_alert_rules(
        owner_scope=ResourceScope.ORGANIZATION,
        organization=organization,
    )


def ensure_default_platform_alert_rules() -> dict[str, Any]:
    """Seed platform defaults without overwriting operator customizations."""
    return create_missing_default_alert_rules(owner_scope=ResourceScope.PLATFORM)


@transaction.atomic
def create_missing_default_alert_rules(
    *,
    owner_scope: str,
    organization=None,
    default_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Create only absent defaults; never restore or re-enable existing rules."""
    if owner_scope == ResourceScope.ORGANIZATION and organization is None:
        raise ValueError("organization is required for organization defaults")
    if owner_scope == ResourceScope.PLATFORM and organization is not None:
        raise ValueError("platform defaults cannot belong to an organization")

    created = 0
    unchanged = 0
    for definition in default_catalog(owner_scope=owner_scope):
        if default_keys is not None and definition["default_key"] not in default_keys:
            continue
        policy = AlertPolicy.objects.filter(
            **_default_policy_lookup(
                owner_scope=owner_scope,
                organization=organization,
                default_key=definition["default_key"],
            )
        ).first()
        if policy is not None:
            unchanged += 1
            continue
        _, was_created = _get_or_create_policy(
            definition,
            organization=organization,
            owner_scope=owner_scope,
        )
        created += int(was_created)
        unchanged += int(not was_created)
    return {
        "created": created,
        "restored": 0,
        "unchanged": unchanged,
        "total": _catalog_size(
            owner_scope=owner_scope,
            default_keys=default_keys,
        ),
    }
