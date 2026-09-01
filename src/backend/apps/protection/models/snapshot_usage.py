from __future__ import annotations

from django.db import models


class SnapshotUsageLease(models.Model):
    """Durable, lightweight protection for a snapshot currently in use."""

    class ConsumerType(models.TextChoices):
        RESTORE = "restore", "Restore"
        CHAT = "chat", "Chat preparation"

    organization_id = models.BigIntegerField(db_index=True)
    snapshot = models.ForeignKey(
        "protection.BackupSourceSnapshot",
        on_delete=models.PROTECT,
        related_name="usage_leases",
    )
    consumer_type = models.CharField(max_length=16, choices=ConsumerType.choices)
    consumer_id = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "protection_snapshot_usage_lease"
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot_id", "consumer_type", "consumer_id"],
                name="uniq_prot_snap_usage_consumer",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization_id", "snapshot_id"],
                name="prot_snap_usage_org_snap_idx",
            ),
        ]
