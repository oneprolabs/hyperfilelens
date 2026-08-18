from __future__ import annotations

from django.db import models


class BackupConfig(models.Model):
    class Status(models.TextChoices):
        PROVISIONING = "provisioning", "Provisioning"
        ACTIVE = "active", "Active"
        PROVISION_FAILED = "provision_failed", "Provision failed"
        RESETTING = "resetting", "Resetting"
        RESET_FAILED = "reset_failed", "Reset failed"

    class CompressionLevel(models.TextChoices):
        NONE = "none", "None"
        BALANCED = "balanced", "Balanced"
        HIGH = "high", "High"

    class RepositoryEndpointType(models.TextChoices):
        EXTERNAL = "external", "External"
        INTERNAL = "internal", "Internal"

    organization_id = models.BigIntegerField(db_index=True)
    name = models.CharField(max_length=200)
    remark = models.TextField(blank=True, default="")
    source_type = models.CharField(max_length=16)
    source_ref_id = models.BigIntegerField()
    repository_id = models.BigIntegerField(db_index=True)
    repository_endpoint_type = models.CharField(
        max_length=16,
        choices=RepositoryEndpointType.choices,
        default=RepositoryEndpointType.EXTERNAL,
    )
    backup_policy_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    file_filter_rule_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    compression_level = models.CharField(
        max_length=20,
        choices=CompressionLevel.choices,
        default=CompressionLevel.BALANCED,
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    reset_task_uuid = models.UUIDField(blank=True, null=True, db_index=True)
    provisioning_task_uuid = models.UUIDField(blank=True, null=True, db_index=True)
    provisioning_error_code = models.CharField(max_length=64, blank=True, default="")
    provisioning_error_message = models.TextField(blank=True, default="")
    recovery_plan_enabled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "protection_backup_config"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization_id", "created_at"],
                name="prot_bcfg_org_cr_idx",
            ),
            models.Index(
                fields=["organization_id", "source_type", "source_ref_id"],
                name="prot_bcfg_org_src_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(compression_level__in=["none", "balanced", "high"]),
                name="prot_bcfg_compression_valid",
            ),
            models.CheckConstraint(
                condition=models.Q(repository_endpoint_type__in=["external", "internal"]),
                name="prot_bcfg_repo_endpoint_valid",
            ),
            models.UniqueConstraint(
                fields=["organization_id", "source_type", "source_ref_id"],
                name="uniq_prot_bcfg_org_source",
            ),
        ]

    def __str__(self) -> str:
        return self.name


class BackupConfigDirectory(models.Model):
    class PathType(models.TextChoices):
        DIRECTORY = "directory", "Directory"
        FILE = "file", "File"
        UNKNOWN = "unknown", "Unknown"

    organization_id = models.BigIntegerField(db_index=True)
    backup_config = models.ForeignKey(
        BackupConfig,
        on_delete=models.CASCADE,
        related_name="directories",
    )
    path = models.CharField(max_length=1000)
    path_type = models.CharField(
        max_length=20,
        choices=PathType.choices,
        default=PathType.UNKNOWN,
    )
    display_name = models.CharField(max_length=300, blank=True, default="")
    estimated_size_bytes = models.BigIntegerField(default=0)
    size_estimated_at = models.DateTimeField(blank=True, null=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "protection_backup_config_directory"
        ordering = ["backup_config_id", "sort_order", "id"]
        indexes = [
            models.Index(
                fields=["backup_config", "path"],
                name="prot_bcdir_cfg_path_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["backup_config", "path"],
                name="uniq_prot_bcdir_path",
            ),
        ]

    def __str__(self) -> str:
        return self.path
