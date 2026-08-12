"""Notification delivery log (alert and platform events)."""

import uuid

from django.conf import settings
from django.db import models

from apps.iam.models import Organization

from apps.notification.constants import NotificationLogStatus, NotificationLogType


class NotificationLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="notification_logs",
    )
    channel = models.ForeignKey(
        "notification.NotificationChannel",
        on_delete=models.CASCADE,
        related_name="logs",
    )
    alert_record_id = models.UUIDField(null=True, blank=True, db_index=True)
    event_type = models.CharField(max_length=120, blank=True, default="", db_index=True)
    notification_type = models.CharField(
        max_length=50,
        choices=NotificationLogType.choices,
        default=NotificationLogType.FIRING,
    )
    status = models.CharField(max_length=50, choices=NotificationLogStatus.choices)
    error_message = models.TextField(blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "notification_logs"
        ordering = ["-sent_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization", "status", "sent_at"],
                name="notif_log_org_st_sent_idx",
            ),
        ]


class UserNotification(models.Model):
    """A user-scoped in-app notification, separate from delivery logs."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="in_app_notifications",
    )
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="user_notifications",
    )
    event_type = models.CharField(max_length=120, db_index=True)
    source_type = models.CharField(max_length=80)
    source_id = models.CharField(max_length=100)
    title = models.CharField(max_length=255)
    summary = models.TextField(blank=True, default="")
    severity = models.CharField(max_length=50, blank=True, default="info")
    target_url = models.CharField(max_length=500, blank=True, default="")
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notification_user"
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "organization", "event_type", "source_type", "source_id"],
                name="uniq_notification_user_event",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "organization", "read_at", "updated_at"],
                name="notif_user_org_read_idx",
            ),
        ]
