"""Persistence models linking HFL tenants to SourceLens resources."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.node.models.base import OrganizationScopedModel


class LensOrgLink(OrganizationScopedModel):
    """Per-organization defaults for SourceLens integration."""

    default_lensnode_uuid = models.UUIDField(null=True, blank=True)
    default_agent_model_ref = models.UUIDField(null=True, blank=True)
    default_multimodal_model_ref = models.UUIDField(null=True, blank=True)
    assistant_name_prefix = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "lens_bridge_org_link"
        constraints = [
            models.UniqueConstraint(
                fields=["organization"],
                name="uniq_lens_bridge_org_link_org",
            ),
        ]

    def resolved_prefix(self) -> str:
        if self.assistant_name_prefix:
            return self.assistant_name_prefix
        return f"hfl-{self.organization_id}"


class LensOrgModelLink(OrganizationScopedModel):
    """Maps an organization to a SourceLens LLMConfig uuid it owns."""

    class DeploymentRole(models.TextChoices):
        AGENT = "agent", "Agent"
        MULTIMODAL = "multimodal", "Multimodal"

    sl_config_uuid = models.UUIDField(db_index=True)
    display_name = models.CharField(max_length=160, blank=True, default="")
    management_key = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )
    deployment_role = models.CharField(
        max_length=16,
        choices=DeploymentRole.choices,
        blank=True,
        default="",
        db_index=True,
    )
    is_deployment_history = models.BooleanField(default=False, db_index=True)
    deployment_fingerprint = models.CharField(max_length=64, blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lens_org_model_links_created",
    )

    class Meta:
        db_table = "lens_bridge_org_model_link"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sl_config_uuid"],
                name="uniq_lens_bridge_org_model_org_uuid",
            ),
            models.UniqueConstraint(
                fields=["sl_config_uuid"],
                name="uniq_lens_bridge_org_model_uuid",
            ),
            models.UniqueConstraint(
                fields=["organization", "management_key"],
                condition=~models.Q(management_key=""),
                name="uniq_lens_borgmdl_org_mgmt_key",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "created_at"],
                name="lens_borgmdl_org_cr_idx",
            ),
        ]


class LensOrgSkillLink(OrganizationScopedModel):
    """Maps an organization to a SourceLens Skill uuid it owns."""

    sl_skill_uuid = models.UUIDField(db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lens_org_skill_links_created",
    )

    class Meta:
        db_table = "lens_bridge_org_skill_link"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sl_skill_uuid"],
                name="uniq_lens_bridge_org_skill_org_uuid",
            ),
            models.UniqueConstraint(
                fields=["sl_skill_uuid"],
                name="uniq_lens_bridge_org_skill_uuid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "created_at"],
                name="lens_borgskill_org_cr_idx",
            ),
        ]


class LensOrgMcpLink(OrganizationScopedModel):
    """Maps an organization to a SourceLens MCP server uuid it owns."""

    sl_mcp_uuid = models.UUIDField(db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lens_org_mcp_links_created",
    )

    class Meta:
        db_table = "lens_bridge_org_mcp_link"
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sl_mcp_uuid"],
                name="uniq_lens_bridge_org_mcp_org_uuid",
            ),
            models.UniqueConstraint(
                fields=["sl_mcp_uuid"],
                name="uniq_lens_bridge_org_mcp_uuid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "created_at"],
                name="lens_borgmcp_org_cr_idx",
            ),
        ]


class LensGatewayLink(OrganizationScopedModel):
    """HFL metadata overlay for a SourceLens-admin LensNode."""

    class GatewayScope(models.TextChoices):
        PLATFORM = "platform", "Platform"
        USER = "user", "User"

    class Origin(models.TextChoices):
        USER = "user", "User"
        PLATFORM = "platform", "Platform"
        EXTERNAL = "external", "External"
        SYSTEM = "system", "System"

    class SidecarStatus(models.TextChoices):
        NOT_DEPLOYED = "not_deployed", "Not deployed"
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        UPGRADING = "upgrading", "Upgrading"
        REMOVING = "removing", "Removing"
        ERROR = "error", "Error"

    gateway = models.ForeignKey(
        "node.Node",
        on_delete=models.CASCADE,
        related_name="lens_gateway_links",
    )
    sl_lensnode_uuid = models.UUIDField(null=True, blank=True, unique=True)
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lens_gateway_links_owned",
    )
    origin = models.CharField(
        max_length=16,
        choices=Origin.choices,
        default=Origin.USER,
        db_index=True,
    )
    workspace_root = models.CharField(max_length=500, blank=True, default="")
    sidecar_status = models.CharField(
        max_length=20,
        choices=SidecarStatus.choices,
        default=SidecarStatus.NOT_DEPLOYED,
        db_index=True,
    )
    config_json = models.JSONField(default=dict, blank=True)
    lensnode_provision_state_json = models.JSONField(default=dict, blank=True)
    lensnode_provision_attempts = models.PositiveIntegerField(default=0)
    lensnode_provision_claim_token = models.UUIDField(
        null=True,
        blank=True,
        unique=True,
    )
    lensnode_provision_claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
    )
    scope = models.CharField(
        max_length=16,
        choices=GatewayScope.choices,
        default=GatewayScope.USER,
        db_index=True,
    )
    is_platform_default = models.BooleanField(default=False, db_index=True)
    # Public Gateway workspace pool (GiB). -1 = unlimited. Meaningful for scope=platform.
    capacity_gb = models.IntegerField(default=-1)

    class Meta:
        db_table = "lens_bridge_gateway_link"
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "gateway"],
                name="uniq_lens_bridge_gw_link_org_gw",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(scope="platform", owner_user_id__isnull=True)
                    | models.Q(scope="user", owner_user_id__isnull=False)
                ),
                name="lens_brgw_scope_owner_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(is_platform_default=False)
                    | models.Q(scope="platform")
                ),
                name="lens_brgw_default_scope_ck",
            ),
            models.UniqueConstraint(
                fields=["organization"],
                condition=models.Q(
                    scope="platform",
                    is_platform_default=True,
                    is_deleted=False,
                ),
                name="uniq_lens_brgw_org_default",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "sidecar_status"],
                name="lens_brgw_org_st_idx",
            ),
            models.Index(
                fields=["scope", "is_platform_default"],
                name="lens_brgw_scope_def_idx",
            ),
        ]

    def resolved_workspace_root(self) -> str:
        if self.workspace_root:
            return self.workspace_root.rstrip("/")
        return f"/workspace/org-{self.organization_id}/data"


class LensKnowledgeSource(OrganizationScopedModel):
    """HFL knowledge source bound to backup path + SL Assistant."""

    class LinkedVersionMode(models.TextChoices):
        LATEST = "latest", "Latest"
        PINNED = "pinned", "Pinned"

    class Status(models.TextChoices):
        SYNCING = "syncing", "Syncing"
        READY = "ready", "Ready"
        DEGRADED = "degraded", "Degraded"
        ERROR = "error", "Error"
        PAUSED = "paused", "Paused"

    class LifecycleStatus(models.TextChoices):
        READY = "ready", "Ready"
        DELETING = "deleting", "Deleting"
        DELETED = "deleted", "Deleted"

    name = models.CharField(max_length=160)
    gateway = models.ForeignKey(
        "node.Node",
        on_delete=models.PROTECT,
        related_name="lens_knowledge_sources",
    )
    gateway_link = models.ForeignKey(
        LensGatewayLink,
        on_delete=models.PROTECT,
        related_name="knowledge_sources",
        help_text="Authoritative gateway authorization used for execution.",
    )
    backup_source_snapshot_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    backup_snapshot_directory_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    source_path = models.CharField(max_length=500)
    source_scopes_json = models.JSONField(default=list, blank=True)
    mount_path_on_gateway = models.CharField(max_length=500, blank=True, default="")
    workspace_path_on_lensnode = models.CharField(max_length=500, blank=True, default="")
    linked_version_mode = models.CharField(
        max_length=16,
        choices=LinkedVersionMode.choices,
        default=LinkedVersionMode.LATEST,
    )
    pinned_snapshot_id = models.BigIntegerField(null=True, blank=True)
    sl_assistant_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    sl_datasource_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    sl_lensnode_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SYNCING,
        db_index=True,
    )
    status_detail = models.TextField(blank=True, default="")
    sync_state_json = models.JSONField(default=dict, blank=True)
    last_restore_record_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    ingest_policy_json = models.JSONField(default=dict, blank=True)
    scan_enabled = models.BooleanField(default=True)
    lifecycle_status = models.CharField(
        max_length=16,
        choices=LifecycleStatus.choices,
        default=LifecycleStatus.READY,
        db_index=True,
    )
    teardown_state_json = models.JSONField(default=dict, blank=True)
    sync_claim_token = models.UUIDField(null=True, blank=True, unique=True)
    sync_claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    sync_next_poll_at = models.DateTimeField(null=True, blank=True, db_index=True)
    teardown_attempts = models.PositiveIntegerField(default=0)
    teardown_claim_token = models.UUIDField(null=True, blank=True, unique=True)
    teardown_claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    teardown_next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lens_knowledge_sources_created",
    )

    class Meta:
        db_table = "lens_bridge_knowledge_source"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization", "status", "created_at"],
                name="lens_bks_org_st_cr_idx",
            ),
            models.Index(
                fields=["organization", "gateway"],
                name="lens_bks_org_gw_idx",
            ),
        ]


class LensWorkspaceBinding(OrganizationScopedModel):
    """Immutable authorization and filesystem identity for one KS workspace."""

    class WorkspaceKind(models.TextChoices):
        MANAGED_RESTORE = "managed_restore", "Managed restore"
        GATEWAY_LOCAL = "gateway_local", "Gateway local"

    class State(models.TextChoices):
        PREPARING = "preparing", "Preparing"
        READY = "ready", "Ready"
        DELETING = "deleting", "Deleting"
        DELETED = "deleted", "Deleted"
        ERROR = "error", "Error"

    class IdentityStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        READY = "ready", "Ready"
        NOT_APPLICABLE = "not_applicable", "Not applicable"
        ERROR = "error", "Error"

    workspace_uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    knowledge_source = models.OneToOneField(
        LensKnowledgeSource,
        on_delete=models.PROTECT,
        related_name="workspace_binding",
    )
    gateway_link = models.ForeignKey(
        LensGatewayLink,
        on_delete=models.PROTECT,
        related_name="workspace_bindings",
    )
    execution_organization_id = models.BigIntegerField(db_index=True)
    execution_node_id = models.BigIntegerField(db_index=True)
    workspace_kind = models.CharField(
        max_length=24,
        choices=WorkspaceKind.choices,
        db_index=True,
    )
    workspace_root = models.CharField(max_length=500)
    relative_path = models.CharField(max_length=500, blank=True, default="")
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.PREPARING,
        db_index=True,
    )
    identity_status = models.CharField(
        max_length=24,
        choices=IdentityStatus.choices,
        default=IdentityStatus.PENDING,
        db_index=True,
    )
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "lens_bridge_workspace_binding"
        constraints = [
            models.UniqueConstraint(
                fields=["gateway_link", "relative_path"],
                condition=~models.Q(relative_path=""),
                name="uniq_lens_workspace_link_path",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        workspace_kind="managed_restore",
                        relative_path__gt="",
                        identity_status__in=["pending", "ready", "error"],
                    )
                    | models.Q(
                        workspace_kind="gateway_local",
                        relative_path="",
                        identity_status="not_applicable",
                    )
                ),
                name="lens_bws_kind_path_identity_ck",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "state"],
                name="lens_bws_org_state_idx",
            ),
            models.Index(
                fields=["execution_node_id", "state"],
                name="lens_bws_node_state_idx",
            ),
        ]

    def resolved_path(self) -> str:
        from apps.lens_bridge.services.gateway_paths import path_within_root

        root = str(self.workspace_root or "").strip()
        if not self.relative_path:
            return path_within_root(root, root, allow_root=True, field="workspace_root")
        relative = str(self.relative_path).strip()
        if relative.startswith("/"):
            raise ValueError("relative_path must be relative")
        parts = relative.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("relative_path contains an unsafe path component")
        return path_within_root(
            f"{root.rstrip('/')}/{relative}",
            root,
            allow_root=False,
            field="workspace_path",
        )


class LensAssistantLink(OrganizationScopedModel):
    """HFL-side visibility and ownership for a SourceLens assistant."""

    class VisibilityScope(models.TextChoices):
        USER = "user", "Only me"
        ORGANIZATION = "organization", "Organization"

    class LifecycleOwner(models.TextChoices):
        MANUAL = "manual", "Manual assistant management"
        CHAT = "chat", "Chat lifecycle"

    sl_assistant_uuid = models.UUIDField(db_index=True)
    knowledge_source = models.ForeignKey(
        LensKnowledgeSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assistant_links",
    )
    visibility_scope = models.CharField(
        max_length=16,
        choices=VisibilityScope.choices,
        default=VisibilityScope.ORGANIZATION,
        db_index=True,
    )
    lifecycle_owner = models.CharField(
        max_length=16,
        choices=LifecycleOwner.choices,
        default=LifecycleOwner.MANUAL,
        db_index=True,
    )
    owner_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lens_assistant_links_owned",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lens_assistant_links_created",
    )

    class Meta:
        db_table = "lens_bridge_assistant_link"
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "sl_assistant_uuid"],
                name="uniq_lens_bridge_asst_link_org_uuid",
            ),
        ]
        indexes = [
            models.Index(
                fields=["organization", "visibility_scope"],
                name="lens_basst_org_scope_idx",
            ),
        ]


class LensSlUserLink(models.Model):
    """Maps an HFL user to a provisioned SourceLens chat-only account."""

    class ProvisionStatus(models.TextChoices):
        READY = "ready", "Ready"
        PENDING = "pending", "Pending"
        ERROR = "error", "Error"

    hfl_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lens_sl_user_link",
    )
    sl_user_id = models.IntegerField(db_index=True)
    sl_username = models.CharField(max_length=150)
    sl_email = models.EmailField(blank=True, default="")
    gateway_operator = models.BooleanField(default=False)
    provision_status = models.CharField(
        max_length=16,
        choices=ProvisionStatus.choices,
        default=ProvisionStatus.PENDING,
        db_index=True,
    )
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "lens_bridge_sl_user_link"
        indexes = [
            models.Index(fields=["sl_user_id"], name="lens_bslusr_sl_uid_idx"),
        ]


class LensChatBinding(OrganizationScopedModel):
    """Copilot context: backup source + snapshot + gateway → KS/Assistant."""

    organization = models.ForeignKey(
        "iam.Organization",
        on_delete=models.CASCADE,
        related_name="%(class)s_set",
    )
    hfl_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lens_chat_bindings",
    )
    backup_config_id = models.BigIntegerField(db_index=True)
    backup_source_snapshot_id = models.BigIntegerField(db_index=True)
    backup_snapshot_directory_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    source_path = models.CharField(max_length=500, blank=True, default="")
    gateway_link = models.ForeignKey(
        LensGatewayLink,
        on_delete=models.PROTECT,
        related_name="chat_bindings",
    )
    knowledge_source = models.ForeignKey(
        LensKnowledgeSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="chat_bindings",
    )
    sl_assistant_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "lens_bridge_chat_binding"
        ordering = ["-updated_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization", "hfl_user", "is_active"],
                name="lens_bcb_org_user_act_idx",
            ),
        ]


class LensSessionLink(OrganizationScopedModel):
    """Maps HFL user sessions to SourceLens sessions (1 Chat ↔ 1 KS+Ass)."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    class LifecycleStatus(models.TextChoices):
        PROVISIONING = "provisioning", "Provisioning"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"
        DELETING = "deleting", "Deleting"
        DELETED = "deleted", "Deleted"

    class GatewaySelectionMode(models.TextChoices):
        AUTO = "auto", "Auto"
        MANUAL = "manual", "Manual"

    class ProvisionPhase(models.TextChoices):
        QUEUED = "queued", "Queued"
        RESTORING = "restoring", "Restoring backup data"
        CONVERTING = "converting", "Extracting document content"
        CREATING_KNOWLEDGE_SOURCE = "creating_knowledge_source", "Creating knowledge source"
        CREATING_ASSISTANT = "creating_assistant", "Creating assistant"
        GRANTING_ASSISTANT = "granting_assistant", "Granting assistant"
        CREATING_SESSION = "creating_session", "Creating chat session"
        READY = "ready", "Ready"
        CLEANING_UP = "cleaning_up", "Cleaning up"
        DELETING = "deleting", "Deleting"
        DELETED = "deleted", "Deleted"

    hfl_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lens_session_links",
    )
    backup_config_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    backup_source_snapshot_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    source_scopes_json = models.JSONField(default=list, blank=True)
    gateway_link = models.ForeignKey(
        LensGatewayLink,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="session_links",
    )
    gateway_selection_mode = models.CharField(
        max_length=16,
        choices=GatewaySelectionMode.choices,
        default=GatewaySelectionMode.AUTO,
    )
    knowledge_source = models.ForeignKey(
        LensKnowledgeSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="session_links",
    )
    sl_session_uuid = models.UUIDField(null=True, blank=True, unique=True, db_index=True)
    sl_assistant_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    agent_model_ref = models.UUIDField(null=True, blank=True, db_index=True)
    multimodal_model_ref = models.UUIDField(null=True, blank=True, db_index=True)
    title = models.CharField(max_length=160, blank=True, default="")
    last_message_at = models.DateTimeField(null=True, blank=True)
    last_assistant_message_at = models.DateTimeField(null=True, blank=True)
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    active_run_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    active_run_status = models.CharField(max_length=16, blank=True, default="")
    chat_binding = models.ForeignKey(
        LensChatBinding,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="session_links",
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    lifecycle_status = models.CharField(
        max_length=16,
        choices=LifecycleStatus.choices,
        default=LifecycleStatus.READY,
        db_index=True,
    )
    lifecycle_error = models.TextField(blank=True, default="")
    provision_phase = models.CharField(
        max_length=32,
        choices=ProvisionPhase.choices,
        default=ProvisionPhase.READY,
        db_index=True,
    )
    provision_detail = models.CharField(max_length=300, blank=True, default="")
    provision_state_json = models.JSONField(default=dict, blank=True)
    provision_attempts = models.PositiveIntegerField(default=0)
    provision_claim_token = models.UUIDField(null=True, blank=True, unique=True)
    provision_claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    provision_next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    teardown_state_json = models.JSONField(default=dict, blank=True)
    teardown_attempts = models.PositiveIntegerField(default=0)
    teardown_claim_token = models.UUIDField(null=True, blank=True, unique=True)
    teardown_claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    teardown_next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        db_table = "lens_bridge_session_link"
        ordering = ["-last_message_at", "-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization", "hfl_user", "status"],
                name="lens_bsess_org_user_st_idx",
            ),
            models.Index(
                fields=["organization", "lifecycle_status"],
                name="lens_bsess_org_lc_idx",
            ),
            models.Index(
                fields=["organization", "hfl_user", "provision_phase"],
                name="lens_bsess_org_user_ph_idx",
            ),
        ]


class LensUsageLedger(OrganizationScopedModel):
    """Authoritative HFL-facing usage record for one SourceLens Q&A run."""

    hfl_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lens_usage_records",
    )
    session_link = models.ForeignKey(
        LensSessionLink,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="usage_records",
    )
    sl_user_id = models.IntegerField(db_index=True)
    sl_run_uuid = models.UUIDField(unique=True, db_index=True)
    sl_session_uuid = models.UUIDField(null=True, blank=True, db_index=True)
    chat_title = models.CharField(max_length=160, blank=True, default="")
    question = models.TextField(blank=True, default="")
    backup_config_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    backup_source_name = models.CharField(max_length=255, blank=True, default="")
    backup_source_snapshot_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    snapshot_created_at = models.DateTimeField(null=True, blank=True)
    source_scopes_json = models.JSONField(default=list, blank=True)
    gateway_selection_mode = models.CharField(max_length=16, blank=True, default="auto")
    gateway_name = models.CharField(max_length=160, blank=True, default="")
    run_status = models.CharField(max_length=24, blank=True, default="queued", db_index=True)
    prompt_tokens = models.BigIntegerField(default=0)
    completion_tokens = models.BigIntegerField(default=0)
    cached_tokens = models.BigIntegerField(default=0)
    reasoning_tokens = models.BigIntegerField(default=0)
    total_tokens = models.BigIntegerField(default=0)
    model_calls = models.PositiveIntegerField(default=0)
    estimated_cost = models.DecimalField(max_digits=18, decimal_places=8, null=True, blank=True)
    cost_currency = models.CharField(max_length=10, blank=True, default="USD")
    call_details_json = models.JSONField(default=list, blank=True)
    run_error = models.TextField(blank=True, default="")
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    source_synced_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reconciliation_attempts = models.PositiveIntegerField(default=0)
    reconciliation_claim_token = models.UUIDField(null=True, blank=True, unique=True)
    reconciliation_claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reconciliation_next_at = models.DateTimeField(null=True, blank=True, db_index=True)
    reconciliation_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "lens_bridge_usage_ledger"
        ordering = ["-occurred_at", "-id"]
        indexes = [
            models.Index(
                fields=["organization", "hfl_user", "occurred_at"],
                name="lens_busg_org_usr_time_idx",
            ),
            models.Index(
                fields=["organization", "sl_user_id", "occurred_at"],
                name="lens_busg_org_slusr_time_idx",
            ),
            models.Index(
                fields=["run_status", "reconciliation_next_at"],
                name="lens_busg_st_recon_idx",
            ),
        ]
