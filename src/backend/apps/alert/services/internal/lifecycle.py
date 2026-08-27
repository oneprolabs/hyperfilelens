"""Alert firing and resolution lifecycle."""

from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from apps.alert.constants import AlertStatus
from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.fingerprint import build_fingerprint
from apps.alert.services.internal.notifier import send_notification, send_resolved_notification
from apps.notification.services.internal.in_app import publish_to_org_members_after_commit


def _publish_in_app(alert: AlertRecord, *, resolved: bool) -> None:
    publish_to_org_members_after_commit(
        organization_id=alert.organization_id,
        event_type="alert.resolved" if resolved else "alert.firing",
        source_type="alert",
        source_id=str(alert.id),
        title=f"Resolved: {alert.title}" if resolved else alert.title,
        summary=alert.resource_name or alert.message or alert.resource_type,
        severity=alert.severity,
        target_url="/ops/alerts",
    )


def _threshold_from_policy(policy) -> Decimal | None:
    rule = policy.trigger_rule or {}
    threshold = rule.get("threshold")
    if threshold is None:
        threshold = rule.get("timeout_seconds")
    if threshold is None:
        return None
    return Decimal(str(threshold))


def _repeat_interval_seconds(policy: AlertPolicy | None) -> int:
    if policy is None:
        return 3600
    rule = policy.trigger_rule or {}
    if "repeat_interval_seconds" not in rule:
        return 3600
    try:
        value = int(rule.get("repeat_interval_seconds") or 0)
    except (TypeError, ValueError):
        return 3600
    if value <= 0:
        return 0
    return max(60, value)


def _notify_on_firing(policy: AlertPolicy | None) -> bool:
    if policy is None:
        return True
    rule = policy.recovery_rule or {}
    return rule.get("notify_on_firing") is not False


def _notify_on_resolved(policy: AlertPolicy | None) -> bool:
    if policy is None:
        return True
    rule = policy.recovery_rule or {}
    return rule.get("notify_on_resolved") is not False


def _should_retry_firing_notification(alert: AlertRecord, policy: AlertPolicy | None) -> bool:
    repeat_interval = _repeat_interval_seconds(policy)
    if repeat_interval <= 0:
        return True
    metadata = alert.metadata or {}
    last_sent = metadata.get("last_notification_at")
    if not last_sent:
        return True
    from django.utils.dateparse import parse_datetime

    sent_at = parse_datetime(str(last_sent))
    if sent_at is None:
        return True
    if timezone.is_naive(sent_at):
        sent_at = timezone.make_aware(sent_at, timezone.get_current_timezone())
    elapsed = (timezone.now() - sent_at).total_seconds()
    return elapsed >= repeat_interval


def _managed_repeat_exhausted(alert: AlertRecord, policy: AlertPolicy | None) -> bool:
    rule = policy.trigger_rule if policy else {}
    if rule.get("managed") != "repository_quota":
        return False
    try:
        return int((alert.metadata or {}).get("repeat_count") or 0) >= int(rule.get("max_repeats") or 5)
    except (TypeError, ValueError):
        return False


def fire_alert(
    policy: AlertPolicy,
    *,
    resource=None,
    title: str = "",
    message: str = "",
    current_value=None,
    alert_key: str = "default",
    metadata=None,
) -> AlertRecord:
    resource_id = getattr(resource, "id", None)
    if resource_id is not None:
        resource_id = str(resource_id)
    resource_name = getattr(resource, "name", None) or getattr(resource, "hostname", None) or ""
    fingerprint = build_fingerprint(policy, resource_id, alert_key)
    now = timezone.now()
    org_id = policy.organization_id

    alert = AlertRecord.objects.filter(
        organization_id=org_id,
        fingerprint=fingerprint,
        status__in=[
            AlertStatus.PENDING,
            AlertStatus.FIRING,
            AlertStatus.ACKNOWLEDGED,
        ],
    ).first()

    threshold = _threshold_from_policy(policy)
    unit = (policy.trigger_rule or {}).get("unit") or (
        "s" if (policy.trigger_rule or {}).get("timeout_seconds") is not None else ""
    )

    if alert:
        previous_status = alert.status
        alert.status = AlertStatus.FIRING
        alert.last_triggered_at = now
        if current_value is not None:
            alert.current_value = Decimal(str(current_value))
        if threshold is not None:
            alert.threshold_value = threshold
        if unit:
            alert.unit = unit
        if title:
            alert.title = title
        if message:
            alert.message = message
        if metadata:
            alert.metadata = {**(alert.metadata or {}), **metadata}
        alert.save(
            update_fields=[
                "status",
                "last_triggered_at",
                "current_value",
                "threshold_value",
                "unit",
                "title",
                "message",
                "metadata",
                "updated_at",
            ]
        )
        if _notify_on_firing(policy) and not _managed_repeat_exhausted(alert, policy) and (
            previous_status != AlertStatus.FIRING or _should_retry_firing_notification(alert, policy)
        ):
            merged = dict(alert.metadata or {})
            merged["repeat_count"] = int(merged.get("repeat_count") or 0) + (1 if previous_status == AlertStatus.FIRING else 0)
            alert.metadata = merged
            alert.save(update_fields=["metadata", "updated_at"])
            send_notification(alert)
        if previous_status != AlertStatus.FIRING:
            _publish_in_app(alert, resolved=False)
        return alert

    alert = AlertRecord.objects.create(
        organization_id=org_id,
        policy_id=policy.id,
        type=policy.type,
        severity=policy.severity,
        status=AlertStatus.FIRING,
        resource_type=policy.resource_type or "",
        resource_id=resource_id or "",
        resource_name=str(resource_name or ""),
        title=title or policy.name,
        message=message,
        current_value=Decimal(str(current_value)) if current_value is not None else None,
        threshold_value=threshold,
        unit=unit,
        fingerprint=fingerprint,
        metadata={**(metadata or {}), "repeat_count": 0},
        first_triggered_at=now,
        last_triggered_at=now,
    )
    if _notify_on_firing(policy):
        send_notification(alert)
    _publish_in_app(alert, resolved=False)
    return alert


def resolve_alert(alert: AlertRecord) -> AlertRecord:
    resolved_at = timezone.now()
    updated = AlertRecord.objects.filter(id=alert.id).exclude(
        status=AlertStatus.RESOLVED,
    ).update(
        status=AlertStatus.RESOLVED,
        resolved_at=resolved_at,
        updated_at=resolved_at,
    )
    if not updated:
        alert.refresh_from_db()
        return alert
    alert.status = AlertStatus.RESOLVED
    alert.resolved_at = resolved_at
    alert.updated_at = resolved_at
    policy = AlertPolicy.objects.filter(id=alert.policy_id).first() if alert.policy_id else None
    if _notify_on_resolved(policy):
        send_resolved_notification(alert)
    _publish_in_app(alert, resolved=True)
    return alert
