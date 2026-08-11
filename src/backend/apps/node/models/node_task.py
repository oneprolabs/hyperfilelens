"""Runtime task dispatched to an Agent (durable command ACK + watchdog)."""

from __future__ import annotations

import uuid

from django.db import models

from .base import OrganizationScopedModel


class NodeTask(OrganizationScopedModel):
    """Control-plane ↔ Agent task; PG is audit, Redis is hot path."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        TIMEOUT = "timeout", "Timeout"
        CANCELED = "canceled", "Canceled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    organization = models.ForeignKey(
        "iam.Organization",
        on_delete=models.CASCADE,
        related_name="node_tasks",
    )
    requesting_organization_id = models.BigIntegerField(db_index=True)
    node = models.ForeignKey(
        "node.Node",
        on_delete=models.CASCADE,
        related_name="node_tasks",
    )

    correlation_type = models.CharField(
        max_length=80,
        blank=True,
        default="",
        db_index=True,
    )
    correlation_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
    )
    parent_task = models.ForeignKey(
        "task.Task",
        on_delete=models.SET_NULL,
        related_name="node_tasks",
        blank=True,
        null=True,
    )

    kind = models.CharField(max_length=120, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    dispatched_at = models.DateTimeField(blank=True, null=True, db_index=True)
    accepted_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_delivery_at = models.DateTimeField(blank=True, null=True, db_index=True)
    delivery_attempt_count = models.PositiveIntegerField(default=0)
    last_progress_at = models.DateTimeField(blank=True, null=True, db_index=True)
    cancel_requested_at = models.DateTimeField(blank=True, null=True, db_index=True)
    watchdog_deadline_at = models.DateTimeField(db_index=True)

    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "node_tasks"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["created_at"], name="node_tasks_created_at_idx"),
            models.Index(
                fields=["organization", "status", "watchdog_deadline_at"],
                name="node_tsk_org_st_wd_idx",
            ),
            models.Index(
                fields=["node", "status"],
                name="node_tsk_node_st_idx",
            ),
            models.Index(
                fields=["correlation_type", "correlation_id"],
                name="node_tsk_corr_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.kind}@{self.pk} ({self.status})"

    def save(self, *args, **kwargs) -> None:
        if self.requesting_organization_id is None:
            self.requesting_organization_id = self.organization_id
        super().save(*args, **kwargs)
