"""Resource metrics scoped to a real tenant or platform infrastructure."""

import uuid

from django.db import models

from apps.iam.models import Organization


class ResourceMetric(models.Model):
    """Time-series sample for a tenant resource (node, repository, etc.)."""

    class Source(models.TextChoices):
        HEARTBEAT = "heartbeat", "Heartbeat"
        COLLECTOR = "collector", "Collector"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="resource_metrics",
        null=True,
        blank=True,
    )
    resource_type = models.CharField(max_length=100, db_index=True)
    resource_id = models.CharField(max_length=128, db_index=True)
    resource_name = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(
        max_length=32,
        choices=Source.choices,
        default=Source.HEARTBEAT,
        db_index=True,
    )
    metrics = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "monitor_resource_metrics"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(
                fields=["organization", "resource_type", "resource_id", "-timestamp"],
                name="mon_res_org_type_id_ts_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.resource_type}:{self.resource_id}@{self.timestamp}"
