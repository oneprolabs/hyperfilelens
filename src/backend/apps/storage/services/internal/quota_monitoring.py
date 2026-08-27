"""Synchronize repository quota monitoring with the alert center."""

from __future__ import annotations

from apps.alert.constants import AlertSeverity, AlertType, ResourceType
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.lifecycle import resolve_alert
from apps.storage.repositories.models import Repository

_NAME_PREFIX = "Repository quota monitoring:"


def _managed_policy(repository: Repository) -> AlertPolicy | None:
    return AlertPolicy.objects.filter(
        organization_id=repository.organization_id,
        name=f"{_NAME_PREFIX} {repository.id}",
        resource_type=ResourceType.BACKUP_REPOSITORY,
    ).first()


def sync_repository_quota_policy(repository: Repository) -> AlertPolicy | None:
    """Create/update or disable the hidden policy represented by repository config."""
    config = repository.config if isinstance(repository.config, dict) else {}
    try:
        quota_gb = float(config.get("quota_gb") or 0)
    except (TypeError, ValueError):
        quota_gb = 0
    enabled = bool(config.get("quota_alert_enabled")) and quota_gb > 0
    policy = _managed_policy(repository)
    if not enabled:
        if policy and policy.enabled:
            policy.enabled = False
            policy.save(update_fields=["enabled", "updated_at"])
            for alert in AlertRecord.objects.filter(
                policy_id=policy.id,
                status__in=["pending", "firing", "acknowledged"],
            ):
                resolve_alert(alert)
        return None
    try:
        threshold = float(config.get("quota_alert_threshold") or 80)
    except (TypeError, ValueError):
        threshold = 80
    threshold = max(1, min(99, threshold))
    if policy is None:
        policy = AlertPolicy.objects.create(
            organization_id=repository.organization_id,
            name=f"{_NAME_PREFIX} {repository.id}",
            description="System-managed quota monitoring policy.",
            type=AlertType.METRIC,
            severity=AlertSeverity.WARNING,
            enabled=True,
            resource_type=ResourceType.BACKUP_REPOSITORY,
            scope="selected",
            resource_ids=[str(repository.id)],
            trigger_rule={
                "metric_key": "capacity_usage",
                "operator": ">=",
                "threshold": threshold,
                "duration_seconds": 0,
                "managed": "repository_quota",
                "repeat_interval_seconds": 3600,
                "max_repeats": 5,
            },
            recovery_rule={"notify_on_firing": True, "notify_on_resolved": True},
            notification_channel_ids=[],
        )
    else:
        policy.enabled = True
        policy.resource_ids = [str(repository.id)]
        rule = dict(policy.trigger_rule or {})
        rule.update(metric_key="capacity_usage", operator=">=", threshold=threshold,
                    duration_seconds=0, managed="repository_quota",
                    repeat_interval_seconds=3600, max_repeats=5)
        policy.trigger_rule = rule
        policy.save(update_fields=["enabled", "resource_ids", "trigger_rule", "updated_at"])
    return policy
