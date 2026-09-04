"""Immutable, tenant-scoped operational events."""

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone


class OperationalEvent(models.Model):
    """A durable record of an operational state change."""

    class Category(models.TextChoices):
        PROTECTION = "protection", "Protection"
        INFRASTRUCTURE = "infrastructure", "Infrastructure"
        SYSTEM = "system", "System"

    class Severity(models.TextChoices):
        INFORMATION = "information", "Information"
        WARNING = "warning", "Warning"
        CRITICAL = "critical", "Critical"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "iam.Organization",
        on_delete=models.CASCADE,
        related_name="operational_events",
    )
    event_type = models.CharField(max_length=120, db_index=True)
    category = models.CharField(max_length=32, choices=Category.choices, db_index=True)
    severity = models.CharField(max_length=20, choices=Severity.choices, db_index=True)
    title = models.CharField(max_length=255)
    details = models.TextField(blank=True, default="")
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    resource_type = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )
    resource_id = models.CharField(max_length=128, blank=True, default="")
    resource_name = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=120, blank=True, default="")
    target_path = models.CharField(max_length=1000, blank=True, default="")
    correlation_id = models.CharField(
        max_length=100, blank=True, default="", db_index=True
    )
    dedup_key = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "monitor_operational_events"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization", "-occurred_at"],
                name="mon_event_org_at_idx",
            ),
            models.Index(
                fields=["organization", "category", "-occurred_at"],
                name="mon_event_org_cat_at_idx",
            ),
            models.Index(
                fields=["organization", "severity", "-occurred_at"],
                name="mon_event_org_sev_at_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "dedup_key"],
                condition=~Q(dedup_key=""),
                name="mon_event_org_dedup_uniq",
            ),
        ]

    def __str__(self) -> str:
        return self.title
