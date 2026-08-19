from __future__ import annotations

from django.db import models

from apps.task.models import Task


class SnapshotDownloadArtifact(models.Model):
    class Status(models.TextChoices):
        UPLOADING = "uploading", "Uploading"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired"
        DELETED = "deleted", "Deleted"

    task = models.OneToOneField(
        Task,
        on_delete=models.CASCADE,
        related_name="snapshot_download_artifact",
    )
    organization_id = models.BigIntegerField(db_index=True)
    source_snapshot_directory_id = models.BigIntegerField(db_index=True)
    relative_path = models.CharField(max_length=1000)
    filename = models.CharField(max_length=300)
    content_type = models.CharField(max_length=120, default="application/octet-stream")
    size_bytes = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    storage_path = models.CharField(max_length=1200)
    upload_token_digest = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.READY,
        db_index=True,
    )
    expires_at = models.DateTimeField(db_index=True)
    downloaded_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "protection_snapshot_download_artifact"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization_id", "status", "expires_at"],
                name="prot_snapdl_org_st_exp_idx",
            ),
            models.Index(
                fields=["organization_id", "source_snapshot_directory_id", "created_at"],
                name="prot_snapdl_org_dir_cr_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.task_id}:{self.filename}"
