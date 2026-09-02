"""Alert policy model."""

import uuid

from django.db import models

from apps.iam.models import Organization

from apps.alert.constants import AlertSeverity, AlertType, PolicyScope, ResourceType


class AlertPolicy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="alert_policies",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    type = models.CharField(max_length=50, choices=AlertType.choices)
    severity = models.CharField(max_length=50, choices=AlertSeverity.choices)
    enabled = models.BooleanField(default=True, db_index=True)
    resource_type = models.CharField(
        max_length=100,
        choices=ResourceType.choices,
        blank=True,
        default="",
    )
    scope = models.CharField(
        max_length=50,
        choices=PolicyScope.choices,
        default=PolicyScope.SELECTED,
    )
    resource_ids = models.JSONField(default=list, blank=True)
    trigger_rule = models.JSONField(default=dict)
    recovery_rule = models.JSONField(null=True, blank=True)
    notification_channel_ids = models.JSONField(default=list, blank=True)
    last_evaluated_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_by = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "alert_policies"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["organization", "enabled"], name="alert_pol_org_en_idx"),
            models.Index(fields=["organization", "type"], name="alert_pol_org_ty_idx"),
        ]

    def __str__(self) -> str:
        return self.name
