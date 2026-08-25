from __future__ import annotations

import uuid

from django.db import models


class RestoreRecord(models.Model):
    class Purpose(models.TextChoices):
        USER_DATA = "user_data", "User data"
        LENS_WORKSPACE = "lens_workspace", "Lens workspace"

    class SourceMode(models.TextChoices):
        PLAN = "plan", "Plan"
        MANUAL = "manual", "Manual"

    class EndpointType(models.TextChoices):
        AGENT = "agent", "Agent"
        NAS = "nas", "NAS"

    class Scope(models.TextChoices):
        SNAPSHOT = "snapshot", "Snapshot"
        PATHS = "paths", "Paths"

    class ConflictMode(models.TextChoices):
        SKIP = "skip", "Skip"
        OVERWRITE = "overwrite", "Overwrite"

    organization_id = models.BigIntegerField(db_index=True)
    requesting_organization_id = models.BigIntegerField(db_index=True)
    target_execution_organization_id = models.BigIntegerField(db_index=True)
    target_execution_node_id = models.BigIntegerField(db_index=True)
    purpose = models.CharField(
        max_length=24,
        choices=Purpose.choices,
        default=Purpose.USER_DATA,
        db_index=True,
    )
    idempotency_key = models.CharField(max_length=160, blank=True, default="")
    workspace_binding_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    restore_uid = models.CharField(max_length=64)
    source_mode = models.CharField(max_length=16, choices=SourceMode.choices, db_index=True)
    plan_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    task_id = models.BigIntegerField(db_index=True)
    task_uuid = models.UUIDField(default=uuid.uuid4, db_index=True)
    source_type = models.CharField(max_length=16, choices=EndpointType.choices)
    source_ref_id = models.BigIntegerField()
    backup_config_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    source_snapshot_id = models.BigIntegerField(db_index=True)
    target_type = models.CharField(max_length=16, choices=EndpointType.choices)
    target_ref_id = models.BigIntegerField()
    target_path = models.CharField(max_length=1000)
    scope = models.CharField(max_length=16, choices=Scope.choices)
    conflict_mode = models.CharField(max_length=16, choices=ConflictMode.choices)
    request_payload = models.JSONField(default=dict, blank=True)
    expanded_payload = models.JSONField(default=dict, blank=True)
    created_by_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "restore_record"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["organization_id", "created_at"], name="restore_rec_org_cr_idx"),
            models.Index(
                fields=["organization_id", "source_type", "source_ref_id", "created_at"],
                name="restore_rec_org_src_cr_idx",
            ),
            models.Index(
                fields=["organization_id", "target_type", "target_ref_id"],
                name="restore_rec_org_tgt_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["organization_id", "restore_uid"],
                name="uniq_restore_record_uid",
            ),
            models.UniqueConstraint(
                fields=["organization_id", "purpose", "idempotency_key"],
                condition=(
                    models.Q(purpose="lens_workspace")
                    & ~models.Q(idempotency_key="")
                ),
                name="uniq_restore_org_purpose_idem",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        purpose="lens_workspace",
                        workspace_binding_id__isnull=False,
                    )
                    & ~models.Q(idempotency_key="")
                    | models.Q(
                        purpose="user_data",
                        workspace_binding_id__isnull=True,
                    )
                ),
                name="restore_purpose_workspace_ck",
            ),
        ]

    def __str__(self) -> str:
        return self.restore_uid


class RestoreRecordItem(models.Model):
    class ConflictMode(models.TextChoices):
        SKIP = "skip", "Skip"
        OVERWRITE = "overwrite", "Overwrite"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"
        CANCELLED = "cancelled", "Cancelled"

    organization_id = models.BigIntegerField(db_index=True)
    restore_record = models.ForeignKey(
        RestoreRecord,
        on_delete=models.CASCADE,
        related_name="items",
    )
    source_snapshot_directory_id = models.BigIntegerField(db_index=True)
    backup_config_dir_id = models.BigIntegerField(db_index=True)
    repository_id = models.BigIntegerField(db_index=True)
    kopia_snapshot_id = models.CharField(max_length=128, db_index=True)
    source_path = models.CharField(max_length=1000)
    selected_paths = models.JSONField(default=list, blank=True)
    target_path = models.CharField(max_length=1000)
    conflict_mode = models.CharField(max_length=16, choices=ConflictMode.choices)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    node_task_id = models.UUIDField(blank=True, null=True, db_index=True)
    last_progress_snapshot = models.JSONField(default=dict, blank=True)
    last_progress_sample = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=80, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    terminal_projection_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "restore_record_item"
        ordering = ["restore_record_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["restore_record", "source_snapshot_directory_id", "target_path"],
                name="uniq_restore_item_dir_target",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.restore_record_id}:{self.source_snapshot_directory_id}"


class DirectNASMount(models.Model):
    """Control-plane aggregate for one Agent-local NAS mount identity."""

    execution_organization_id = models.BigIntegerField(db_index=True)
    requesting_organization_id = models.BigIntegerField(db_index=True)
    repository_id = models.BigIntegerField(db_index=True)
    reader_node_id = models.BigIntegerField(db_index=True)
    mount_point = models.CharField(max_length=1000)
    mount_key = models.CharField(max_length=64)
    cleanup_node_task_id = models.UUIDField(blank=True, null=True, db_index=True)
    cleanup_after = models.DateTimeField(blank=True, null=True, db_index=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "restore_direct_nas_mount"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "execution_organization_id",
                    "repository_id",
                    "reader_node_id",
                    "mount_key",
                ],
                name="uniq_restore_direct_nas_mount",
            ),
        ]
        indexes = [
            models.Index(
                fields=["reader_node_id", "repository_id"],
                name="rst_nas_mount_node_repo_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.reader_node_id}:{self.repository_id}:{self.mount_point}"


class DirectNASMountLease(models.Model):
    """One Chat restore's use of a directly mounted NAS repository."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RELEASED = "released", "Released"
        CLEANUP_PENDING = "cleanup_pending", "Cleanup pending"

    organization_id = models.BigIntegerField(db_index=True)
    restore_record = models.ForeignKey(
        RestoreRecord,
        on_delete=models.CASCADE,
        related_name="direct_nas_mount_leases",
    )
    mount = models.ForeignKey(
        DirectNASMount,
        on_delete=models.CASCADE,
        related_name="leases",
    )
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    cleanup_node_task_id = models.UUIDField(blank=True, null=True, db_index=True)
    released_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "restore_direct_nas_mount_lease"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "restore_record",
                    "mount",
                ],
                name="uniq_restore_direct_nas_lease",
            ),
        ]
        indexes = [
            models.Index(
                fields=["mount", "status"],
                name="rst_nas_lease_mount_status_idx",
            ),
            models.Index(
                fields=["restore_record", "status"],
                name="rst_nas_lease_rec_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.mount_id}:{self.restore_record_id}:{self.status}"
