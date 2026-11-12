"""Deliver alert notifications via the notification app."""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.alert.models import AlertPolicy, AlertRecord
from apps.alert.services.internal.alert_email import (
    build_alert_email_model,
    send_organization_alert_email,
)
from apps.alert.services.internal.delivery_access import can_deliver_alert
from apps.configuration.services.runtime_settings import email_delivery_configured
from apps.iam.models import Organization
from apps.iam.services.organization_owner import resolve_organization_owner_user
from apps.notification.constants import NotificationLogStatus as NotifyStatus
from apps.notification.constants import NotificationLogType as NotifyType
from apps.notification.models import NotificationChannel, NotificationDelivery, NotificationLog
from apps.notification.services.interface import attempt_delivery
from common.platform_staff import has_installation_identity
from common.resource_scope import ResourceScope

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
    if policy.organization_id is not None:
        from common.extension_spi import get_authz_provider
        gate = getattr(get_authz_provider(), "authorized_alert_channels", None)
        if callable(gate):
            return gate(policy, ids)
    return ids


def _payload_for_alert(alert: AlertRecord, *, resolved: bool) -> dict:
    policy = _policy_for_alert(alert)
    return {
        "alert_id": str(alert.id),
        "policy_id": str(alert.policy_id) if alert.policy_id else None,
        "policy_name": policy.name if policy else "",
        "policy_type": policy.type if policy else (alert.type or ""),
        "severity": alert.severity,
        "status": alert.status,
        "title": alert.title,
        "message": alert.message,
        "resource_type": alert.resource_type,
        "resource_id": alert.resource_id,
        "resource_name": alert.resource_name,
        "resolved": resolved,
    }


def _mark_notified(
    alert: AlertRecord,
    *,
    extra: dict | None = None,
    touch_last_notification: bool = True,
) -> None:
    metadata = dict(alert.metadata or {})
    if touch_last_notification:
        metadata["last_notification_at"] = timezone.now().isoformat()
    if extra:
        metadata.update(extra)
    alert.metadata = metadata
    alert.save(update_fields=["metadata", "updated_at"])


def _owner_fallback_eligible(alert: AlertRecord, policy: AlertPolicy | None) -> bool:
    if policy is None or not policy.default_key:
        return False
    if alert.owner_scope != ResourceScope.ORGANIZATION or not alert.organization_id:
        return False
    return True


def _send_owner_email_fallback(alert: AlertRecord, *, resolved: bool) -> None:
    """Email the organization Owner for unbound org default rules."""
    policy = _policy_for_alert(alert)
    if not _owner_fallback_eligible(alert, policy):
        return
    if not email_delivery_configured():
        logger.info(
            "org default alert skip owner email: email service not configured alert=%s",
            alert.id,
        )
        return

    organization = Organization.objects.filter(id=alert.organization_id).first()
    if organization is None:
        return
    owner = resolve_organization_owner_user(organization)
    if owner is None:
        logger.warning(
            "org default alert skip owner email: no owner org=%s alert=%s",
            organization.key,
            alert.id,
        )
        return
    if has_installation_identity(owner):
        logger.info(
            "org default alert skip owner email: owner is installation admin org=%s alert=%s",
            organization.key,
            alert.id,
        )
        return
    if not can_deliver_alert(alert, recipient_user_id=owner.pk):
        return
    recipient = str(getattr(owner, "email", "") or "").strip()
    if not recipient:
        logger.warning(
            "org default alert skip owner email: no owner email org=%s alert=%s",
            organization.key,
            alert.id,
        )
        return

    try:
        email_model = build_alert_email_model(
            title=str(alert.title or (policy.name if policy else "") or "Alert"),
            severity=str(alert.severity or ""),
            resolved=resolved,
            organization_name=organization.name or "",
            organization_key=organization.key or "",
            rule_name=str((policy.name if policy else "") or alert.title or ""),
            resource_type=str(alert.resource_type or ""),
            resource_name=str(alert.resource_name or ""),
            resource_id=str(alert.resource_id or ""),
            message=str(alert.message or ""),
            metadata=dict(alert.metadata or {}),
            current_value=alert.current_value,
            threshold_value=alert.threshold_value,
            unit=str(alert.unit or ""),
            first_triggered_at=alert.first_triggered_at or alert.created_at,
            resolved_at=alert.resolved_at,
        )
        send_organization_alert_email(recipient=recipient, model=email_model)
    except Exception:  # noqa: BLE001
        logger.exception(
            "org default alert owner email failed alert=%s recipient=%s",
            alert.id,
            recipient,
        )
        _mark_notified(
            alert,
            extra={
                "owner_email_fallback": {
                    "status": "failed",
                    "recipient": recipient,
                    "resolved": resolved,
                }
            },
            touch_last_notification=False,
        )
        return

    _mark_notified(
        alert,
        extra={
            "owner_email_fallback": {
                "status": "sent",
                "recipient": recipient,
                "resolved": resolved,
            }
        },
    )


def _dispatch(alert: AlertRecord, *, resolved: bool) -> None:
    if not can_deliver_alert(alert):
        return
    channel_ids = _channel_ids_for_alert(alert)
    if not channel_ids:
        _send_owner_email_fallback(alert, resolved=resolved)
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
        if not can_deliver_alert(alert, channel_id=channel_id):
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

    _mark_notified(alert)


def send_notification(alert: AlertRecord) -> None:
    _dispatch(alert, resolved=False)


def send_resolved_notification(alert: AlertRecord) -> None:
    _dispatch(alert, resolved=True)
