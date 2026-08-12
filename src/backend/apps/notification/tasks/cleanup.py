"""Retention cleanup for notification tables."""

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from common.observability.celery_context import logged_celery_task

from apps.notification.models import (
    NotificationDelivery,
    NotificationLog,
    UserNotification,
)


@shared_task(name="apps.notification.tasks.cleanup.purge_old_notification_records")
@logged_celery_task(
    name="apps.notification.tasks.cleanup.purge_old_notification_records",
    trace_keys=("days_to_keep",),
)
def purge_old_notification_records(*, days_to_keep: int = 90) -> dict[str, int]:
    """Delete notification history and inbox items beyond retention."""
    cutoff = timezone.now() - timedelta(days=int(days_to_keep))
    logs_deleted, _ = NotificationLog.objects.filter(sent_at__lt=cutoff).delete()
    deliveries_deleted, _ = NotificationDelivery.objects.filter(created_at__lt=cutoff).delete()
    user_notifications_deleted, _ = UserNotification.objects.filter(
        updated_at__lt=cutoff,
    ).delete()
    return {
        "logs_deleted": logs_deleted,
        "deliveries_deleted": deliveries_deleted,
        "user_notifications_deleted": user_notifications_deleted,
    }
