"""One optional Enterprise gate for immediate, resolved and queued sends."""

from typing import Any
from uuid import UUID

from common.extension_spi import get_authz_provider


def can_deliver_alert(
    alert: Any, *, channel_id: int | None = None,
    recipient_user_id: int | None = None,
) -> bool:
    """Community and platform delivery are unchanged; tenant sends recheck."""
    if alert.organization_id is None:
        return True
    gate = getattr(get_authz_provider(), "can_deliver_alert", None)
    return not callable(gate) or gate(
        alert, channel_id=channel_id, recipient_user_id=recipient_user_id,
    )


def can_deliver_notification(delivery: Any) -> bool:
    """Resolve queued alert content against its current tenant and channel."""
    if (
        get_authz_provider() is None
        or delivery.organization_id is None
        or delivery.event_type not in ("alert.firing", "alert.resolved")
    ):
        return True
    from apps.alert.models import AlertRecord

    payload = delivery.payload if isinstance(delivery.payload, dict) else {}
    try:
        alert_id = UUID(str(payload.get("alert_id")))
    except (ValueError, TypeError, AttributeError):
        return False
    alert = AlertRecord.objects.filter(
        pk=alert_id, organization_id=delivery.organization_id,
        owner_scope=delivery.owner_scope,
    ).first()
    if alert is None or any(
        str(payload.get(field) or "") != str(getattr(alert, field) or "")
        for field in ("policy_id", "resource_type", "resource_id")
    ):
        return False
    return can_deliver_alert(alert, channel_id=delivery.channel_id)
