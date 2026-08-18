"""
Organization-scoped license models (aligned with xxz license management).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.iam.models import Organization
from apps.subscription.constants import DEFAULT_LIMITS


class License(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        REVOKED = "revoked", "Revoked"

    class ChangeType(models.TextChoices):
        INITIAL = "initial", "Initial"
        RENEWAL = "renewal", "Renewal"
        UPGRADE = "upgrade", "Upgrade"
        DOWNGRADE = "downgrade", "Downgrade"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    license_key = models.CharField(max_length=64, unique=True, db_index=True)
    version = models.PositiveIntegerField(default=1)
    change_type = models.CharField(
        max_length=20,
        choices=ChangeType.choices,
        default=ChangeType.INITIAL,
    )
    change_reason = models.CharField(max_length=200, blank=True, default="")

    machine_code = models.CharField(max_length=64, db_index=True)
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="license",
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="activated_licenses",
    )

    max_organizations = models.IntegerField(default=1)
    max_users = models.IntegerField(default=50)
    max_nodes = models.IntegerField(default=20)
    max_storage_gb = models.IntegerField(default=500)
    max_gateways = models.IntegerField(default=5)
    # Instance count of Public (platform) Gateways — not org-split.
    max_public_gateways = models.IntegerField(
        default=DEFAULT_LIMITS["max_public_gateways"]
    )
    # Instance-wide hard limit for actual Public Gateway capacity use (bytes).
    max_public_gateway_capacity_bytes = models.BigIntegerField(
        default=DEFAULT_LIMITS["max_public_gateway_capacity_bytes"]
    )
    # Instance workload pools. Keep these separate from storage capacity so a
    # license can express both resource-count and managed-data boundaries.
    max_source_nas = models.IntegerField(default=DEFAULT_LIMITS["max_source_nas"])
    max_object_storage = models.IntegerField(
        default=DEFAULT_LIMITS["max_object_storage"]
    )
    max_target_nas = models.IntegerField(default=DEFAULT_LIMITS["max_target_nas"])
    max_standalone_disk = models.IntegerField(
        default=DEFAULT_LIMITS["max_standalone_disk"]
    )
    max_protected_sources = models.IntegerField(
        default=DEFAULT_LIMITS["max_protected_sources"]
    )
    # Lifetime AI token budget (LensUsageLedger.total_tokens), not request count.
    ai_insights_quota = models.IntegerField(default=50_000_000)
    max_tasks = models.IntegerField(default=50)
    max_alert_policies = models.IntegerField(default=50)
    # Opaque Enterprise feature keys. ``*`` grants every feature.
    features = models.JSONField(default=list, blank=True)

    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    signature = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )

    class Meta:
        db_table = "subscription_license"
        ordering = ["-activated_at"]

    def __str__(self):
        return f"{self.license_key[:16]}… ({self.organization_id})"

    @property
    def is_valid(self) -> bool:
        if self.status != self.Status.ACTIVE:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    @property
    def is_perpetual(self) -> bool:
        return self.expires_at is None

    @property
    def days_until_expiry(self) -> int:
        if not self.expires_at:
            return -1
        delta = self.expires_at - timezone.now()
        return max(0, delta.days)

    def get_limits(self) -> dict:
        return {
            **DEFAULT_LIMITS,
            "max_organizations": self.max_organizations,
            "max_users": self.max_users,
            "max_nodes": self.max_nodes,
            "max_storage_gb": self.max_storage_gb,
            "max_gateways": self.max_gateways,
            "max_public_gateways": self.max_public_gateways,
            "max_public_gateway_capacity_bytes": self.max_public_gateway_capacity_bytes,
            "max_source_nas": self.max_source_nas,
            "max_object_storage": self.max_object_storage,
            "max_target_nas": self.max_target_nas,
            "max_standalone_disk": self.max_standalone_disk,
            "max_protected_sources": self.max_protected_sources,
            "ai_insights_quota": self.ai_insights_quota,
            "ai_tokens": self.ai_insights_quota,
            "max_tasks": self.max_tasks,
            "max_alert_policies": self.max_alert_policies,
        }

    def archive_to_history(self, *, change_type: str, reason: str = "", changed_by=None):
        LicenseHistory.objects.create(
            license_key=self.license_key,
            version=self.version,
            machine_code=self.machine_code,
            organization=self.organization,
            activated_by=self.activated_by,
            changed_by=changed_by,
            max_organizations=self.max_organizations,
            max_users=self.max_users,
            max_nodes=self.max_nodes,
            max_storage_gb=self.max_storage_gb,
            max_gateways=self.max_gateways,
            max_public_gateways=self.max_public_gateways,
            max_public_gateway_capacity_bytes=self.max_public_gateway_capacity_bytes,
            max_source_nas=self.max_source_nas,
            max_object_storage=self.max_object_storage,
            max_target_nas=self.max_target_nas,
            max_standalone_disk=self.max_standalone_disk,
            max_protected_sources=self.max_protected_sources,
            ai_insights_quota=self.ai_insights_quota,
            max_tasks=self.max_tasks,
            max_alert_policies=self.max_alert_policies,
            features=list(self.features or []),
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            activated_at=self.activated_at,
            archived_at=timezone.now(),
            status=self.status,
            signature=self.signature,
            change_type=change_type,
            change_reason=reason,
        )


class LicenseHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    license_key = models.CharField(max_length=64, db_index=True)
    version = models.PositiveIntegerField(default=1)
    machine_code = models.CharField(max_length=64)
    organization = models.ForeignKey(
        Organization,
        on_delete=models.SET_NULL,
        null=True,
        related_name="license_history",
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="license_history_activated",
    )
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="license_history_changed",
    )
    max_organizations = models.IntegerField()
    max_users = models.IntegerField()
    max_nodes = models.IntegerField()
    max_storage_gb = models.IntegerField()
    max_gateways = models.IntegerField()
    max_public_gateways = models.IntegerField(default=DEFAULT_LIMITS["max_public_gateways"])
    max_public_gateway_capacity_bytes = models.BigIntegerField(
        default=DEFAULT_LIMITS["max_public_gateway_capacity_bytes"]
    )
    max_source_nas = models.IntegerField(default=DEFAULT_LIMITS["max_source_nas"])
    max_object_storage = models.IntegerField(
        default=DEFAULT_LIMITS["max_object_storage"]
    )
    max_target_nas = models.IntegerField(default=DEFAULT_LIMITS["max_target_nas"])
    max_standalone_disk = models.IntegerField(
        default=DEFAULT_LIMITS["max_standalone_disk"]
    )
    max_protected_sources = models.IntegerField(
        default=DEFAULT_LIMITS["max_protected_sources"]
    )
    ai_insights_quota = models.IntegerField()
    max_tasks = models.IntegerField()
    max_alert_policies = models.IntegerField()
    features = models.JSONField(default=list, blank=True)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField()
    archived_at = models.DateTimeField()
    status = models.CharField(max_length=20)
    signature = models.TextField(blank=True, default="")
    change_type = models.CharField(max_length=20)
    change_reason = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "subscription_license_history"
        ordering = ["-archived_at"]


class MachineCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=64, unique=True)
    organization = models.OneToOneField(
        Organization,
        on_delete=models.CASCADE,
        related_name="machine_code_record",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="machine_codes",
    )
    hostname = models.CharField(max_length=100, blank=True, default="")
    source = models.CharField(max_length=50, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "subscription_machine_code"
        ordering = ["-created_at"]
