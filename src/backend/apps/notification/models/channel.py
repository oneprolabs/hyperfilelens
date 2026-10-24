from django.db import models

from apps.iam.models import Organization
from common.resource_scope import ResourceScope

from apps.notification.constants import ChannelType


class NotificationChannel(models.Model):
    Type = ChannelType
    OwnerScope = ResourceScope

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="notification_channels",
        null=True,
        blank=True,
    )
    owner_scope = models.CharField(
        max_length=16,
        choices=ResourceScope.choices,
        default=ResourceScope.ORGANIZATION,
        db_index=True,
    )
    name = models.CharField(max_length=200)
    channel_type = models.CharField(max_length=50, choices=Type.choices, db_index=True)
    config = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "notification_channels"
        ordering = ["organization_id", "name", "id"]
        indexes = [
            models.Index(fields=["owner_scope", "is_active"], name="notif_ch_owner_active_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(owner_scope=ResourceScope.ORGANIZATION, organization__isnull=False)
                    | models.Q(owner_scope=ResourceScope.PLATFORM, organization__isnull=True)
                ),
                name="notif_channel_owner_scope_ck",
            ),
        ]


class NotificationDelivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    OwnerScope = ResourceScope

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="notification_deliveries",
        null=True,
        blank=True,
    )
    owner_scope = models.CharField(
        max_length=16,
        choices=ResourceScope.choices,
        default=ResourceScope.ORGANIZATION,
        db_index=True,
    )
    channel = models.ForeignKey(
        NotificationChannel,
        on_delete=models.CASCADE,
        related_name="deliveries",
    )
    event_type = models.CharField(max_length=120, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "notification_deliveries"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization", "status", "created_at"],
                name="notif_del_org_status_cr_idx",
            ),
            models.Index(
                fields=["owner_scope", "status", "created_at"],
                name="notif_del_owner_st_cr_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(owner_scope=ResourceScope.ORGANIZATION, organization__isnull=False)
                    | models.Q(owner_scope=ResourceScope.PLATFORM, organization__isnull=True)
                ),
                name="notif_delivery_owner_scope_ck",
            ),
        ]
