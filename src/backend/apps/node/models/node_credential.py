"""Long-lived per-node credentials issued after enrollment."""

from __future__ import annotations

import hashlib
import secrets

from django.db import models
from django.utils import timezone

from .base import NodeRole, OrganizationScopedModel


def hash_node_secret(secret: str) -> str:
    """Return a stable digest for a high-entropy node or session secret."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class NodeCredential(OrganizationScopedModel):
    """Revocable long-lived credential bound to exactly one registered node."""

    organization = models.ForeignKey(
        "iam.Organization",
        on_delete=models.CASCADE,
        related_name="node_credentials",
        null=True,
        blank=True,
    )
    node = models.OneToOneField(
        "node.Node",
        on_delete=models.CASCADE,
        related_name="credential",
    )
    enrollment_token = models.ForeignKey(
        "node.NodeToken",
        on_delete=models.SET_NULL,
        related_name="issued_credentials",
        blank=True,
        null=True,
    )
    role = models.CharField(max_length=20, choices=NodeRole.choices)
    installation_id = models.CharField(
        max_length=128, blank=True, default="", db_index=True
    )
    secret_prefix = models.CharField(max_length=16, db_index=True)
    secret_hash = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True, db_index=True)
    last_used_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "node_credentials"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(organization__isnull=False) | models.Q(role="gateway"),
                name="node_credential_platform_role",
            ),
        ]
        ordering = ["organization_id", "node_id"]

    @staticmethod
    def generate_secret() -> str:
        return "hfln_" + secrets.token_urlsafe(32)

    def set_secret(self, secret: str) -> None:
        self.secret_prefix = secret[:12]
        self.secret_hash = hash_node_secret(secret)

    def matches(self, secret: str) -> bool:
        if not secret or not secrets.compare_digest(self.secret_prefix, secret[:12]):
            return False
        return secrets.compare_digest(self.secret_hash, hash_node_secret(secret))


class NodeInstallationSession(OrganizationScopedModel):
    """Short-lived resumable authorization for one host installation."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        COMPLETED = "completed", "Completed"
        RELEASED = "released", "Released"

    organization = models.ForeignKey(
        "iam.Organization",
        on_delete=models.CASCADE,
        related_name="node_installation_sessions",
        null=True,
        blank=True,
    )
    enrollment_token = models.ForeignKey(
        "node.NodeToken",
        on_delete=models.CASCADE,
        related_name="installation_sessions",
    )
    role = models.CharField(max_length=20, choices=NodeRole.choices)
    installation_id = models.CharField(max_length=128, db_index=True)
    secret_prefix = models.CharField(max_length=16, db_index=True)
    secret_hash = models.CharField(max_length=64, unique=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    last_activity_at = models.DateTimeField(default=timezone.now, db_index=True)
    idle_expires_at = models.DateTimeField(db_index=True)
    absolute_expires_at = models.DateTimeField(db_index=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = "node_installation_sessions"
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(
                fields=["enrollment_token", "status", "idle_expires_at"],
                name="node_inst_session_active_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(organization__isnull=False) | models.Q(role="gateway"),
                name="node_session_platform_role",
            ),
            models.UniqueConstraint(
                fields=["enrollment_token", "role", "installation_id"],
                condition=models.Q(status="active"),
                name="node_inst_session_active_uniq",
            ),
        ]

    @staticmethod
    def generate_secret() -> str:
        return "hfls_" + secrets.token_urlsafe(32)

    def set_secret(self, secret: str) -> None:
        self.secret_prefix = secret[:12]
        self.secret_hash = hash_node_secret(secret)

    def matches(self, secret: str) -> bool:
        if not secret or not secrets.compare_digest(self.secret_prefix, secret[:12]):
            return False
        return secrets.compare_digest(self.secret_hash, hash_node_secret(secret))
