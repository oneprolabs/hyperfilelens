"""Enrollment token for node install scripts."""

from __future__ import annotations

import secrets
from typing import Any

from django.conf import settings
from django.db import models

from .base import NodeInstallationMode, NodeRole, OrganizationScopedModel


class NodeToken(OrganizationScopedModel):
    """Time-bounded enrollment token used to start host installations."""

    class EnrollmentMode(models.TextChoices):
        LEGACY = "legacy", "Legacy"
        CURRENT = "current", "Current"

    class InstallationModePolicy(models.TextChoices):
        FIXED = "fixed", "Fixed"
        AUTO = "auto", "Automatic"

    class TargetPlatform(models.TextChoices):
        LINUX = "linux", "Linux"
        WINDOWS = "windows", "Windows"
        MACOS = "macos", "macOS"

    organization = models.ForeignKey(
        "iam.Organization",
        on_delete=models.CASCADE,
        related_name="node_tokens",
        null=True,
        blank=True,
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    role = models.CharField(max_length=20, choices=NodeRole.choices)
    installation_mode = models.CharField(
        max_length=16,
        choices=NodeInstallationMode.choices,
        default=NodeInstallationMode.SYSTEM,
        db_index=True,
        help_text="Installation and runtime mode authorized by this token.",
    )
    installation_mode_policy = models.CharField(
        max_length=16,
        choices=InstallationModePolicy.choices,
        default=InstallationModePolicy.FIXED,
        db_index=True,
        help_text=(
            "Fixed authorizes installation_mode. Automatic authorizes the "
            "platform-specific mode selected from the install process identity."
        ),
    )
    target_platform = models.CharField(
        max_length=16,
        choices=TargetPlatform.choices,
        blank=True,
        default="",
        help_text="Operating system bound to an automatic source-Agent token.",
    )
    note = models.CharField(max_length=200, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    expires_at = models.DateTimeField(blank=True, null=True, db_index=True)
    used_at = models.DateTimeField(blank=True, null=True)
    enrollment_mode = models.CharField(
        max_length=16,
        choices=EnrollmentMode.choices,
        default=EnrollmentMode.CURRENT,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="node_tokens_created",
    )
    gateway_scope = models.CharField(max_length=16, blank=True, default="")
    transport_namespace = models.CharField(
        max_length=128, blank=True, default="", db_index=True, editable=False,
        help_text="Stable control-plane routing namespace authorized by this token.",
    )

    class Meta:
        db_table = "node_tokens"
        ordering = ["-created_at", "id"]
        indexes = [
            models.Index(
                fields=["organization", "role", "is_active"],
                name="node_tkn_org_role_act_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(organization__isnull=False) & ~models.Q(gateway_scope="platform")
                    | (
                        models.Q(organization__isnull=True, gateway_scope="platform", role="gateway")
                        & ~models.Q(transport_namespace="")
                    )
                ),
                name="node_token_resource_owner_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(transport_namespace="")
                    | models.Q(role=NodeRole.GATEWAY, gateway_scope="platform")
                ),
                name="node_token_transport_platform",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(installation_mode=NodeInstallationMode.SYSTEM)
                    | models.Q(role=NodeRole.AGENT)
                ),
                name="node_token_user_mode_agent_only",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        installation_mode_policy="fixed",
                        target_platform="",
                    )
                    | models.Q(
                        installation_mode_policy="auto",
                        role=NodeRole.AGENT,
                        target_platform__in=("linux", "windows", "macos"),
                    )
                ),
                name="node_token_auto_mode_platform",
            ),
        ]

    @staticmethod
    def generate_token() -> str:
        return secrets.token_hex(32)

    @property
    def transport_key(self) -> str:
        """Keep install commands and signed URLs stable across ownership changes."""
        return self.transport_namespace or self.organization.key

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.token:
            self.token = self.generate_token()
        if self.gateway_scope == "platform" and not self.transport_namespace:
            from apps.node.services.internal.transport_identity import PLATFORM_TRANSPORT_NAMESPACE

            self.transport_namespace = PLATFORM_TRANSPORT_NAMESPACE
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                    "transport_namespace",
                }
        super().save(*args, **kwargs)
