from __future__ import annotations

import uuid
from decimal import Decimal

from django.db import models


class Task(models.Model):
    class Type(models.TextChoices):
        BACKUP = "backup", "Backup"
        RESTORE = "restore", "Restore"
        SNAPSHOT_DOWNLOAD = "snapshot_download", "Snapshot download"
        SNAPSHOT_DELETE = "snapshot_delete", "Snapshot delete"
        BACKUP_CONFIG_RESET = "backup_config_reset", "Backup config reset"
        SOURCE_UNREGISTER = "source_unregister", "Source unregister"
        NODE_LIFECYCLE = "node_lifecycle", "Node lifecycle"
        REPOSITORY_OPERATION = "repository_operation", "Repository operation"
        STORAGE_PROVIDER_VALIDATION = (
            "storage_provider_validation",
            "Storage provider validation",
        )

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        WAITING = "waiting", "Waiting"
        BLOCKED = "blocked", "Blocked"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        TIMEOUT = "timeout", "Timeout"

    class TriggerType(models.TextChoices):
        MANUAL = "manual", "Manual"
        SYSTEM = "system", "System"
        RETRY = "retry", "Retry"
        API = "api", "API"
        HOOK = "hook", "Hook"

    task_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    organization_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    task_type = models.CharField(max_length=32, choices=Type.choices, db_index=True)
    display_name = models.CharField(max_length=255, db_index=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    progress = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    current_step = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    retry_count = models.IntegerField(default=0)
    recovery_attempt = models.PositiveIntegerField(default=0)
    replaces_task = models.OneToOneField(
        "self",
        on_delete=models.SET_NULL,
        related_name="replacement_task",
        blank=True,
        null=True,
    )
    trigger_type = models.CharField(
        max_length=16,
        choices=TriggerType.choices,
        default=TriggerType.MANUAL,
        db_index=True,
    )
    request_payload = models.JSONField(blank=True, null=True)
    group_uuid = models.UUIDField(blank=True, null=True, db_index=True)
    idempotency_key = models.CharField(max_length=128, blank=True, null=True)
    result_payload = models.JSONField(blank=True, null=True)
    error_code = models.CharField(max_length=64, blank=True, null=True, db_index=True)
    error_message = models.TextField(blank=True, null=True)
    started_at = models.DateTimeField(blank=True, null=True, db_index=True)
    finished_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "task"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["organization_id", "status", "created_at"], name="task_org_status_created_idx"),
            models.Index(fields=["organization_id", "task_type", "status"], name="task_org_type_status_idx"),
            models.Index(fields=["organization_id", "trigger_type", "created_at"], name="task_org_trigger_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(progress__gte=0) & models.Q(progress__lte=100),
                name="task_progress_range",
            ),
            models.UniqueConstraint(
                fields=["organization_id", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="task_org_idempotency_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.task_type}:{self.task_uuid} ({self.status})"

    @property
    def name(self) -> str:
        return self.display_name
