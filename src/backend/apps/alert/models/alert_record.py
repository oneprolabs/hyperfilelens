"""Alert record (firing incident) model."""

import uuid

from django.db import models

from apps.iam.models import Organization
from common.resource_scope import ResourceScope

from apps.alert.constants import AlertSeverity, AlertStatus, AlertType, ResourceType


class AlertRecord(models.Model):
    OwnerScope = ResourceScope

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="alert_records",
        null=True,
        blank=True,
    )
    owner_scope = models.CharField(
        max_length=16,
        choices=ResourceScope.choices,
        default=ResourceScope.ORGANIZATION,
        db_index=True,
    )
    policy_id = models.UUIDField(null=True, blank=True, db_index=True)
    type = models.CharField(max_length=50, choices=AlertType.choices)
    severity = models.CharField(max_length=50, choices=AlertSeverity.choices)
    status = models.CharField(
        max_length=50,
        choices=AlertStatus.choices,
        default=AlertStatus.FIRING,
        db_index=True,
    )
    resource_type = models.CharField(
        max_length=100,
        choices=ResourceType.choices,
        blank=True,
        default="",
    )
    resource_id = models.CharField(max_length=64, blank=True, default="")
    resource_name = models.CharField(max_length=255, blank=True, default="")
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True, default="")
    current_value = models.DecimalField(
        max_digits=20, decimal_places=4, null=True, blank=True
    )
    threshold_value = models.DecimalField(
        max_digits=20, decimal_places=4, null=True, blank=True
    )
    unit = models.CharField(max_length=50, blank=True, default="")
    fingerprint = models.CharField(max_length=255, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    first_triggered_at = models.DateTimeField(null=True, blank=True)
    last_triggered_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.BigIntegerField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "alert_records"
        ordering = ["-last_triggered_at", "-created_at"]
        indexes = [
            models.Index(
                fields=["organization", "status", "created_at"],
                name="alert_rec_org_st_idx",
            ),
            models.Index(
                fields=["owner_scope", "status", "created_at"],
                name="alert_rec_owner_st_idx",
            ),
            models.Index(fields=["fingerprint"], name="alert_rec_fp_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(owner_scope=ResourceScope.ORGANIZATION, organization__isnull=False)
                    | models.Q(owner_scope=ResourceScope.PLATFORM, organization__isnull=True)
                ),
                name="alert_record_owner_scope_ck",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def duration_seconds(self) -> int | None:
        if not self.first_triggered_at:
            return None
        end_at = self.resolved_at or self.last_triggered_at or self.updated_at
        return int((end_at - self.first_triggered_at).total_seconds())
