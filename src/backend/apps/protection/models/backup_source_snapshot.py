from __future__ import annotations

import uuid

from django.db import models


class BackupSourceSnapshot(models.Model):
    class Status(models.TextChoices):
        CREATING = "creating", "Creating"
        AVAILABLE = "available", "Available"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"
        DELETING = "deleting", "Deleting"
        DELETE_FAILED = "delete_failed", "Delete failed"
        DELETED = "deleted", "Deleted"

    class TriggerType(models.TextChoices):
        MANUAL = "manual", "Manual"
        SCHEDULE = "schedule", "Schedule"
        RETRY = "retry", "Retry"
        API = "api", "API"

    organization_id = models.BigIntegerField(db_index=True)
    snapshot_uid = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=128)
    source_type = models.CharField(max_length=16)
    source_ref_id = models.BigIntegerField()
    backup_config_id = models.BigIntegerField(db_index=True)
    repository_id = models.BigIntegerField(db_index=True)
    task_id = models.BigIntegerField(db_index=True)
    task_uuid = models.UUIDField(default=uuid.uuid4, db_index=True)
    trigger_type = models.CharField(
        max_length=20,
        choices=TriggerType.choices,
        default=TriggerType.MANUAL,
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATING,
        db_index=True,
    )
    started_at = models.DateTimeField(blank=True, null=True, db_index=True)
    finished_at = models.DateTimeField(blank=True, null=True, db_index=True)
    directory_count = models.IntegerField(default=0)
    successful_directory_count = models.IntegerField(default=0)
    failed_directory_count = models.IntegerField(default=0)
    total_size_bytes = models.BigIntegerField(default=0)
    file_count = models.BigIntegerField(default=0)
    dir_count = models.BigIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    policy_snapshot = models.JSONField(default=dict, blank=True)
    deleted_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "protection_backup_source_snapshot"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization_id", "source_type", "source_ref_id", "created_at"],
                name="prot_bsrcsnap_org_src_cr_idx",
            ),
            models.Index(
                fields=["organization_id", "backup_config_id", "created_at"],
                name="prot_bsrcsnap_org_cfg_cr_idx",
            ),
            models.Index(
                fields=["organization_id", "status", "created_at"],
                name="prot_bsrcsnap_org_st_cr_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization_id", "snapshot_uid"],
                name="uniq_prot_bsrcsnap_uid",
            ),
            models.UniqueConstraint(
                fields=["organization_id", "idempotency_key"],
                name="uniq_prot_bsrcsnap_idem",
            ),
            models.UniqueConstraint(
                fields=["organization_id", "backup_config_id"],
                condition=models.Q(status="creating"),
                name="uniq_prot_bsrcsnap_active_cfg",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.snapshot_uid} ({self.status})"


class BackupSourceSnapshotDirectory(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHING = "dispatching", "Dispatching"
        RUNNING = "running", "Running"
        CREATING = "creating", "Creating"
        AVAILABLE = "available", "Available"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"
        DELETED = "deleted", "Deleted"

    class PathType(models.TextChoices):
        DIRECTORY = "directory", "Directory"
        FILE = "file", "File"
        UNKNOWN = "unknown", "Unknown"

    source_snapshot = models.ForeignKey(
        BackupSourceSnapshot,
        on_delete=models.CASCADE,
        related_name="directories",
    )
    organization_id = models.BigIntegerField(db_index=True)
    backup_config_id = models.BigIntegerField(db_index=True)
    backup_config_dir_id = models.BigIntegerField(db_index=True)
    source_path = models.CharField(max_length=1000)
    path_type = models.CharField(
        max_length=20,
        choices=PathType.choices,
        default=PathType.UNKNOWN,
    )
    display_name = models.CharField(max_length=300, blank=True, default="")
    repository_id = models.BigIntegerField(db_index=True)
    repository_locator = models.JSONField(default=dict, blank=True)
    kopia_snapshot_id = models.CharField(max_length=128, blank=True, null=True, db_index=True)
    node_task_id = models.UUIDField(blank=True, null=True, db_index=True)
    retry_count = models.IntegerField(default=0)
    dispatched_at = models.DateTimeField(blank=True, null=True)
    last_substantive_progress_at = models.DateTimeField(blank=True, null=True)
    last_progress_snapshot = models.JSONField(default=dict, blank=True)
    last_progress_sample = models.JSONField(default=dict, blank=True)
    adopted_late_result = models.BooleanField(default=False)
    cancel_requested_at = models.DateTimeField(blank=True, null=True)
    stall_warned_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    size_bytes = models.BigIntegerField(default=0)
    new_original_content_bytes = models.BigIntegerField(blank=True, null=True)
    new_packed_content_bytes = models.BigIntegerField(blank=True, null=True)
    file_count = models.BigIntegerField(default=0)
    dir_count = models.BigIntegerField(default=0)
    stats = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=80, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "protection_backup_source_snapshot_directory"
        ordering = ["source_snapshot_id", "id"]
        indexes = [
            models.Index(
                fields=["organization_id", "backup_config_dir_id", "created_at"],
                name="prot_bssd_org_cfg_cr_idx",
            ),
            models.Index(
                fields=["organization_id", "repository_id", "kopia_snapshot_id"],
                name="prot_bssd_org_repo_kid_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["source_snapshot", "backup_config_dir_id"],
                name="uniq_prot_bsrcsnapdir_cfgdir",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.source_snapshot_id}:{self.backup_config_dir_id}:{self.status}"
