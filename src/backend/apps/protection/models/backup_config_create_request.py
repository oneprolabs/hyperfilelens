from __future__ import annotations

from django.db import models


class BackupConfigCreateRequest(models.Model):
    """Durable result of one organization-scoped idempotent create request."""

    organization_id = models.BigIntegerField(db_index=True)
    idempotency_key = models.CharField(max_length=128)
    request_digest = models.CharField(max_length=64)
    backup_config = models.ForeignKey(
        "protection.BackupConfig",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="create_requests",
    )
    response_status = models.PositiveSmallIntegerField(blank=True, null=True)
    response_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "protection_backup_config_create_request"
        constraints = [
            models.UniqueConstraint(
                fields=["organization_id", "idempotency_key"],
                name="uniq_prot_bcfg_create_org_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization_id", "created_at"],
                name="prot_bcfg_create_org_cr_idx",
            ),
        ]
