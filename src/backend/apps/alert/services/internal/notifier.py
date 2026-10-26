"""Deliver alert notifications via the notification app."""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.alert.models import AlertPolicy, AlertRecord
from apps.notification.constants import NotificationLogStatus as NotifyStatus
from apps.notification.constants import NotificationLogType as NotifyType
from apps.notification.models import NotificationChannel, NotificationDelivery, NotificationLog
from apps.notification.services.interface import attempt_delivery

logger = logging.getLogger(__name__)


def _policy_for_alert(alert: AlertRecord) -> AlertPolicy | None:
    if not alert.policy_id:
        return None
    return AlertPolicy.objects.filter(id=alert.policy_id).first()


def _channel_ids_for_alert(alert: AlertRecord) -> list[int]:
    policy = _policy_for_alert(alert)
    if not policy:
        return []
    raw = policy.notification_channel_ids or []
    ids: list[int] = []
    for item in raw:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


def _payload_for_alert(alert: AlertRecord, *, resolved: bool) -> dict:
    return {
        "alert_id": str(alert.id),
        "policy_id": str(alert.policy_id) if alert.policy_id else None,
        "severity": alert.severity,
        "status": alert.status,
        "title": alert.title,
        "message": alert.message,
        "resource_type": alert.resource_type,
        "resource_id": alert.resource_id,
        "resource_name": alert.resource_name,
        "resolved": resolved,
    }


def _dispatch(alert: AlertRecord, *, resolved: bool) -> None:
    channel_ids = _channel_ids_for_alert(alert)
    if not channel_ids:
        return
    event_type = "alert.resolved" if resolved else "alert.firing"
    notification_type = NotifyType.RESOLVED if resolved else NotifyType.FIRING
    payload = _payload_for_alert(alert, resolved=resolved)

    for channel_id in channel_ids:
        channel = NotificationChannel.objects.filter(
            id=channel_id,
            owner_scope=alert.owner_scope,
            organization_id=alert.organization_id,
            is_active=True,
        ).first()
        if channel is None:
            continue
        log_status = NotifyStatus.FAILED
        error_message = ""
        try:
            delivery = NotificationDelivery.objects.create(
                owner_scope=alert.owner_scope,
                organization_id=alert.organization_id,
                channel_id=channel.id,
                event_type=event_type,
                payload=payload,
                status=NotificationDelivery.Status.PENDING,
            )
            result = attempt_delivery(delivery=delivery)
            log_status = NotifyStatus.SUCCESS if result.ok else NotifyStatus.FAILED
            error_message = result.error or ""
        except Exception as exc:  # noqa: BLE001
            logger.exception("alert notification failed channel=%s", channel_id)
            error_message = str(exc)

        NotificationLog.objects.create(
            owner_scope=alert.owner_scope,
            organization_id=alert.organization_id,
            channel_id=channel.id,
            alert_record_id=alert.id,
            event_type=event_type,
            notification_type=notification_type,
            status=log_status,
            error_message=error_message,
            payload=payload,
        )

    metadata = dict(alert.metadata or {})
    metadata["last_notification_at"] = timezone.now().isoformat()
    alert.metadata = metadata
    alert.save(update_fields=["metadata", "updated_at"])


def send_notification(alert: AlertRecord) -> None:
    _dispatch(alert, resolved=False)


def send_resolved_notification(alert: AlertRecord) -> None:
    _dispatch(alert, resolved=True)
