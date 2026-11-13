"""Route deliveries to channel adapters."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.utils import timezone

from apps.notification.channels import EmailChannel, SmsChannel, WebhookChannel
from apps.notification.channels.webhook import webhook_url
from apps.notification.constants import ChannelType
from apps.notification.exceptions import ChannelError
from apps.notification.models import NotificationChannel, NotificationDelivery

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryAttemptResult:
    delivery: NotificationDelivery
    ok: bool
    error: str = ""


def _adapter_for(channel_type: str) -> EmailChannel | SmsChannel | WebhookChannel:
    if channel_type == ChannelType.EMAIL:
        return EmailChannel()
    if channel_type == ChannelType.SMS:
        return SmsChannel()
    if channel_type in (ChannelType.WEBHOOK, ChannelType.DINGTALK, ChannelType.WECOM):
        return WebhookChannel()
    raise ChannelError(f"unsupported channel_type: {channel_type}")


def _channel_for_delivery(channel: NotificationChannel) -> NotificationChannel:
    if channel.channel_type == ChannelType.WEBHOOK:
        return channel
    if channel.channel_type in (ChannelType.DINGTALK, ChannelType.WECOM):
        cfg = dict(channel.config or {})
        cfg["url"] = webhook_url(cfg)
        cfg["webhook_platform"] = channel.channel_type
        return NotificationChannel(
            organization_id=channel.organization_id,
            name=channel.name,
            channel_type=ChannelType.WEBHOOK,
            config=cfg,
            is_active=channel.is_active,
        )
    return channel


def attempt_delivery(*, delivery: NotificationDelivery) -> DeliveryAttemptResult:
    channel = delivery.channel
    try:
        from apps.alert.services.internal.delivery_access import can_deliver_notification

        if not can_deliver_notification(delivery):
            raise ChannelError("Alert delivery is no longer authorized.")
        adapter = _adapter_for(channel.channel_type)
        adapter.send(channel=_channel_for_delivery(channel), delivery=delivery)
        delivery.status = NotificationDelivery.Status.SENT
        delivery.error = ""
        delivery.sent_at = timezone.now()
        delivery.save(update_fields=["status", "error", "sent_at"])
        return DeliveryAttemptResult(delivery=delivery, ok=True)
    except Exception as exc:
        msg = str(exc)
        logger.warning("Notification delivery failed: %s", msg)
        delivery.status = NotificationDelivery.Status.FAILED
        delivery.error = msg[:2000]
        delivery.save(update_fields=["status", "error"])
        return DeliveryAttemptResult(delivery=delivery, ok=False, error=msg)
