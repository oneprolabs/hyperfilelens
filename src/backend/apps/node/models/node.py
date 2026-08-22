"""Node registry model."""

from django.db import models
from django.utils import timezone

from .base import NodeInstallationMode, NodeRole, OrganizationScopedModel


class Node(OrganizationScopedModel):
    """Registered Agent endpoint (agent, proxy, or gateway)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        UPGRADING = "upgrading", "Upgrading"
        RESTARTING = "restarting", "Restarting"
        VERIFYING = "verifying", "Verifying"
        VERIFICATION_PENDING = "verification_pending", "Verification pending"
        REMOVING = "removing", "Removing"
        CLEANING_UP = "cleaning_up", "Cleaning up"
        FAILED = "failed", "Failed"
        UPGRADE_FAILED = "upgrade_failed", "Upgrade Failed"
        DEREGISTRATION_FAILED = "deregistration_failed", "Deregistration Failed"

    class Availability(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"

    Role = NodeRole
    InstallationMode = NodeInstallationMode

    id = models.BigAutoField(primary_key=True)

    organization = models.ForeignKey(
        "iam.Organization",
        on_delete=models.CASCADE,
        related_name="nodes",
    )
    name = models.CharField(max_length=200)
    role = models.CharField(max_length=20, choices=Role.choices)
    installation_mode = models.CharField(
        max_length=16,
        choices=InstallationMode.choices,
        default=InstallationMode.SYSTEM,
        db_index=True,
        help_text="Immutable protection mode selected for one Agent installation.",
    )
    version = models.CharField(max_length=50, blank=True, default="")
    os_name = models.CharField(max_length=80, blank=True, default="")
    installation_id = models.CharField(
        max_length=128, blank=True, default="", db_index=True
    )
    host_fingerprint = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text=(
            "Non-authoritative product-scoped digest used only for host correlation; "
            "duplicate values are expected after reinstallation."
        ),
    )
    ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        help_text="Agent-reported primary host address.",
    )
    repository_server_address = models.CharField(
        max_length=253,
        blank=True,
        default="",
        help_text=(
            "Optional source-reachable address advertised by a Proxy Repository Server."
        ),
    )
    connection_ip_address = models.GenericIPAddressField(
        blank=True,
        null=True,
        help_text="Latest HTTP/WebSocket source address observed by the control plane.",
    )
    network_inventory = models.JSONField(
        default=dict,
        blank=True,
        help_text="Bounded current Agent network-interface snapshot.",
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    availability = models.CharField(
        max_length=20,
        choices=Availability.choices,
        default=Availability.OFFLINE,
        db_index=True,
    )
    availability_updated_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
    )
    last_seen_at = models.DateTimeField(blank=True, null=True, db_index=True)
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text=("Agent-reported extension data (labels, env, install hints, etc.)."),
    )

    class Meta:
        db_table = "node_nodes"
        ordering = ["organization_id", "name", "id"]
        indexes = [
            models.Index(
                fields=["organization", "role", "status"],
                name="node_nd_org_role_st_idx",
            ),
            models.Index(
                fields=["organization", "last_seen_at"],
                name="node_nd_org_seen_idx",
            ),
        ]
        constraints = [
            # host_fingerprint is deliberately not unique: each fresh local
            # installation keeps the old Node and creates a new record.
            models.CheckConstraint(
                condition=(
                    models.Q(installation_mode=NodeInstallationMode.SYSTEM)
                    | models.Q(role=NodeRole.AGENT)
                ),
                name="node_user_mode_source_agent_only",
            ),
            models.UniqueConstraint(
                fields=["organization", "role", "installation_id"],
                condition=models.Q(is_deleted=False) & ~models.Q(installation_id=""),
                name="node_unique_installation_identity",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.role})"

    def soft_delete(self) -> None:
        """Revoke the node credential as part of logical node removal."""
        super().soft_delete()
        from .node_credential import NodeCredential

        now = timezone.now()
        NodeCredential.objects.filter(node_id=self.pk, is_active=True).update(
            is_active=False,
            revoked_at=now,
            updated_at=now,
        )
