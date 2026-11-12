"""Enriched notification log payloads for API responses."""

from __future__ import annotations

from apps.alert.models import AlertPolicy, AlertRecord
from apps.notification.models import NotificationChannel, NotificationLog


def notification_log_detail(
    log: NotificationLog,
    *,
    alert_record=None,
    policy=None,
    channel=None,
    resolved=False,
) -> dict:
    if not resolved and alert_record is None and log.alert_record_id:
        alert_record = AlertRecord.objects.filter(id=log.alert_record_id).first()
    if channel is None:
        channel = log.channel
    if not resolved and policy is None and alert_record and alert_record.policy_id:
        policy = AlertPolicy.objects.filter(id=alert_record.policy_id).first()

    return {
        "id": str(log.id),
        "channel_id": channel.id if channel else None,
        "alert_record_id": str(log.alert_record_id) if log.alert_record_id else None,
        "notification_type": log.notification_type,
        "status": log.status,
        "error_message": log.error_message,
        "event_type": log.event_type,
        "created_at": log.sent_at.isoformat(),
        "sent_at": log.sent_at.isoformat(),
        "channel": {
            "id": channel.id,
            "name": channel.name,
            "type": channel.channel_type,
            "enabled": channel.is_active,
        }
        if channel
        else None,
        "alert": {
            "id": str(alert_record.id),
            "title": alert_record.title,
            "message": alert_record.message,
            "type": alert_record.type,
            "severity": alert_record.severity,
            "status": alert_record.status,
            "resource_type": alert_record.resource_type,
            "resource_id": alert_record.resource_id,
            "resource_name": alert_record.resource_name,
        }
        if alert_record
        else None,
        "policy": {
            "id": str(policy.id),
            "name": policy.name,
            "type": policy.type,
            "severity": policy.severity,
            "enabled": policy.enabled,
        }
        if policy
        else None,
    }


def notification_log_details(logs: list[NotificationLog], *, request=None) -> list[dict]:
    alert_ids = [log.alert_record_id for log in logs if log.alert_record_id]
    channel_ids = [log.channel_id for log in logs]
    alert_records = AlertRecord.objects.filter(id__in=alert_ids)
    alert_map = {record.id: record for record in alert_records}
    policy_ids = [r.policy_id for r in alert_records if r.policy_id]
    policies = AlertPolicy.objects.filter(id__in=policy_ids)
    if request is not None:
        from apps.iam.resource_access import filter_resource_queryset
        policies = filter_resource_queryset(request, policies, "alert_policy", action="resource.view")
    policy_map = {p.id: p for p in policies}
    channel_map = {
        ch.id: ch for ch in NotificationChannel.objects.filter(id__in=channel_ids)
    }
    results = []
    for log in logs:
        alert_record = alert_map.get(log.alert_record_id) if log.alert_record_id else None
        policy = None
        if alert_record and alert_record.policy_id:
            policy = policy_map.get(alert_record.policy_id)
        results.append(
            notification_log_detail(
                log,
                alert_record=alert_record,
                policy=policy,
                channel=channel_map.get(log.channel_id),
                resolved=True,
            )
        )
    return results
