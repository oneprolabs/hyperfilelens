from apps.notification.models import NotificationDelivery


def task_alert_title(delivery: NotificationDelivery) -> str:
    payload = delivery.payload or {}
    if delivery.event_type in ("alert.firing", "alert.resolved") and payload.get("policy_type") == "task":
        return " ".join(str(payload.get("title") or "").split())
    return ""
