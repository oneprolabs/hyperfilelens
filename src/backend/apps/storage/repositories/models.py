import json

from django.db import models

from apps.storage.crypto import decrypt_text, encrypt_text


class Credential(models.Model):
    class Type(models.TextChoices):
        S3 = "s3", "S3 Access Key"
        SMB = "smb", "SMB User"
        REPO_PASSWORD = "repo_password", "Repository Password"
        API_TOKEN = "api_token", "API Token"

    organization_id = models.BigIntegerField(db_index=True)
    credential_type = models.CharField(max_length=30, choices=Type.choices)
    secret_cipher = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "credential"
        ordering = ["organization_id", "credential_type", "id"]
        indexes = [
            models.Index(
                fields=["organization_id", "credential_type"],
                name="credential_org_type_idx",
            ),
        ]

    def set_secret(self, plaintext: str) -> None:
        token = encrypt_text(plaintext or "")
        self.secret_cipher = {"alg": "fernet", "ciphertext": token} if token else {}

    def get_secret(self) -> str:
        if not isinstance(self.secret_cipher, dict):
            return ""
        return decrypt_text(str(self.secret_cipher.get("ciphertext") or ""))

    def set_secret_payload(self, payload: dict) -> None:
        normalized = payload if isinstance(payload, dict) else {}
        plaintext = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        token = encrypt_text(plaintext)
        self.secret_cipher = {
            "alg": "fernet-json-v1",
            "key_version": "v1",
            "ciphertext": token,
        }

    def get_secret_payload(self) -> dict:
        if not isinstance(self.secret_cipher, dict):
            return {}
        alg = str(self.secret_cipher.get("alg") or "")
        plaintext = decrypt_text(str(self.secret_cipher.get("ciphertext") or ""))
        if not plaintext:
            return {}
        if alg == "fernet-json-v1":
            value = json.loads(plaintext)
            return value if isinstance(value, dict) else {}
        # Backward compatibility for legacy single-secret credentials.
        return {"secret": plaintext}


class Repository(models.Model):
    class MetricProbeStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
    class Type(models.TextChoices):
        S3 = "s3", "S3"
        NAS = "nas", "NAS"
        PROXY_FS = "proxy_fs", "Proxy Filesystem"

    class Status(models.TextChoices):
        CREATING = "creating", "Creating"
        CREATE_FAILED = "create_failed", "Create failed"
        CREATED = "created", "Created"
        REMOVING = "removing", "Removing"
        REMOVE_FAILED = "remove_failed", "Remove failed"
        REMOVED = "removed", "Removed"

    class Health(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        UNVERIFIED = "unverified", "Unverified"

    class NasProtocol(models.TextChoices):
        SMB = "smb", "SMB"
        NFS = "nfs", "NFS"

    class BindNodeType(models.TextChoices):
        PROXY = "proxy", "Proxy"

    class S3Platform(models.TextChoices):
        AWS = "aws", "AWS"
        HUAWEICLOUD = "huaweicloud", "Huawei Cloud"
        ALIYUN = "aliyun", "Aliyun"
        CUSTOM = "custom", "Custom"

    class S3BucketMode(models.TextChoices):
        EXISTING = "existing", "Existing bucket"
        NEW = "new", "Bucket created for this repository"

    class CleanupResult(models.TextChoices):
        DELETED = "deleted", "Physical repository deleted"
        FORCE_SKIPPED = "force_skipped", "Physical repository retained"
        PRESERVED = "preserved", "Legacy physical repository preserved"

    organization_id = models.BigIntegerField(db_index=True)
    name = models.CharField(max_length=200)
    repo_type = models.CharField(max_length=20, choices=Type.choices)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATING,
        db_index=True,
    )
    health = models.CharField(
        max_length=20,
        choices=Health.choices,
        default=Health.OFFLINE,
        db_index=True,
    )
    health_failures = models.PositiveSmallIntegerField(default=0)
    config = models.JSONField(default=dict, blank=True)
    credential_id = models.BigIntegerField(blank=True, null=True)
    capacity_bytes = models.BigIntegerField(default=0)
    estimated_usage_bytes = models.BigIntegerField(default=0)
    physical_usage_bytes = models.BigIntegerField(blank=True, null=True)
    storage_total_bytes = models.BigIntegerField(default=0)
    storage_used_bytes = models.BigIntegerField(default=0)
    storage_available_bytes = models.BigIntegerField(default=0)
    storage_pool_key = models.CharField(max_length=500, blank=True, default="")
    storage_mount_point = models.CharField(max_length=1000, blank=True, default="")
    last_checked_at = models.DateTimeField(blank=True, null=True, db_index=True)
    metrics_last_attempt_at = models.DateTimeField(blank=True, null=True, db_index=True)
    usage_probe_status = models.CharField(
        max_length=20, choices=MetricProbeStatus.choices, default=MetricProbeStatus.PENDING
    )
    usage_last_success_at = models.DateTimeField(blank=True, null=True)
    usage_last_error = models.CharField(max_length=1000, blank=True, default="")
    capacity_probe_status = models.CharField(
        max_length=20, choices=MetricProbeStatus.choices, default=MetricProbeStatus.PENDING
    )
    capacity_last_success_at = models.DateTimeField(blank=True, null=True)
    capacity_last_error = models.CharField(max_length=1000, blank=True, default="")
    nas_protocol = models.CharField(
        max_length=20,
        choices=NasProtocol.choices,
        blank=True,
        null=True,
    )
    bind_node_type = models.CharField(
        max_length=20,
        choices=BindNodeType.choices,
        blank=True,
        null=True,
    )
    bind_node_id = models.BigIntegerField(blank=True, null=True)
    s3_platform = models.CharField(
        max_length=50,
        blank=True,
        null=True,
    )
    s3_bucket = models.CharField(max_length=100, blank=True, null=True)
    s3_bucket_mode = models.CharField(
        max_length=16,
        choices=S3BucketMode.choices,
        default=S3BucketMode.EXISTING,
    )
    removed_at = models.DateTimeField(blank=True, null=True, db_index=True)
    cleanup_result = models.CharField(
        max_length=24,
        choices=CleanupResult.choices,
        blank=True,
        default="",
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "storage_repository"
        ordering = ["organization_id", "name", "id"]
        indexes = [
            models.Index(
                fields=["organization_id", "repo_type", "status"],
                name="storage_rep_organ_8d0473_idx",
            ),
            models.Index(
                fields=["organization_id", "health"],
                name="storage_rep_organ_fefeb5_idx",
            ),
            models.Index(
                fields=["organization_id", "bind_node_type", "bind_node_id"],
                name="storage_rep_organ_6f3912_idx",
            ),
            models.Index(
                fields=["organization_id", "s3_platform", "s3_bucket"],
                name="storage_rep_organ_93d157_idx",
            ),
        ]


class RepositoryUsageShard(models.Model):
    class Scope(models.TextChoices):
        DIRECT_NAS_AGENT = "direct_nas_agent", "Direct NAS Agent"

    class Status(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    organization_id = models.BigIntegerField(db_index=True)
    repository_id = models.BigIntegerField(db_index=True)
    usage_scope = models.CharField(
        max_length=40,
        choices=Scope.choices,
        default=Scope.DIRECT_NAS_AGENT,
    )
    node_id = models.BigIntegerField(db_index=True)
    repository_subdir = models.CharField(max_length=500)
    mount_point = models.CharField(max_length=1000, blank=True, default="")
    estimated_usage_bytes = models.BigIntegerField(default=0)
    capacity_bytes = models.BigIntegerField(default=0)
    physical_usage_bytes = models.BigIntegerField(blank=True, null=True)
    source_config_count = models.IntegerField(default=0)
    source_config_ids = models.JSONField(default=list, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.SKIPPED,
        db_index=True,
    )
    last_error = models.CharField(max_length=1000, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    last_checked_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_success_checked_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "storage_repository_usage_shard"
        ordering = ["organization_id", "repository_id", "usage_scope", "node_id", "id"]
        indexes = [
            models.Index(
                fields=["organization_id", "repository_id", "usage_scope"],
                name="storage_rus_org_repo_scope_idx",
            ),
            models.Index(
                fields=["organization_id", "node_id"],
                name="storage_rus_org_node_idx",
            ),
            models.Index(
                fields=["last_checked_at"],
                name="storage_rus_checked_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "organization_id",
                    "repository_id",
                    "usage_scope",
                    "node_id",
                    "repository_subdir",
                ],
                name="uniq_storage_rus_scope_node",
            ),
        ]


class RepositoryExecutionTarget(models.Model):
    """Physical repository boundary used for ownership and exclusive operations."""

    class OwnerType(models.TextChoices):
        CONTROLLER = "controller", "Controller"
        NODE = "node", "Node"

    organization_id = models.BigIntegerField(db_index=True)
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="execution_targets",
    )
    target_key = models.CharField(max_length=700, unique=True)
    owner_type = models.CharField(max_length=20, choices=OwnerType.choices)
    owner_node_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    owner_identity = models.CharField(max_length=255)
    repository_subdir = models.CharField(max_length=500, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    active_task = models.OneToOneField(
        "task.Task",
        on_delete=models.SET_NULL,
        related_name="locked_repository_target",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "storage_repository_execution_target"
        ordering = ["organization_id", "repository_id", "target_key"]
        indexes = [
            models.Index(
                fields=["organization_id", "repository", "is_active"],
                name="stor_ret_org_repo_act_idx",
            ),
            models.Index(
                fields=["owner_type", "owner_node_id", "is_active"],
                name="storage_ret_owner_active_idx",
            ),
        ]


class RepositoryTask(models.Model):
    """Repository-operation metadata extending the platform Task lifecycle."""

    class OperationType(models.TextChoices):
        MAINTENANCE_QUICK = "maintenance.quick", "Quick maintenance"
        MAINTENANCE_FULL = "maintenance.full", "Full maintenance"
        CLEANUP_TARGET = "cleanup.target", "Delete subrepository"
        CLEANUP_REPOSITORY = "cleanup.repository", "Delete repository"
        CHECK = "check", "Check"
        CREATE_REPOSITORY = "create.repository", "Create repository"
        REPAIR_BIND = "repair.bind", "Bind proxy"
        REPAIR_REMOUNT = "repair.remount", "Remount repository"

    task = models.OneToOneField(
        "task.Task",
        on_delete=models.CASCADE,
        related_name="repository_operation",
    )
    repository = models.ForeignKey(
        Repository,
        on_delete=models.CASCADE,
        related_name="repository_tasks",
    )
    execution_target = models.ForeignKey(
        RepositoryExecutionTarget,
        on_delete=models.PROTECT,
        related_name="repository_tasks",
        blank=True,
        null=True,
    )
    triggered_by_task = models.ForeignKey(
        "task.Task",
        on_delete=models.SET_NULL,
        related_name="triggered_repository_operations",
        blank=True,
        null=True,
    )
    force = models.BooleanField(default=False)
    requested_by_id = models.BigIntegerField(blank=True, null=True)
    operation_type = models.CharField(max_length=64, choices=OperationType.choices, db_index=True)
    owner_type = models.CharField(max_length=20, choices=RepositoryExecutionTarget.OwnerType.choices)
    owner_node_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    owner_identity = models.CharField(max_length=255)
    due_at = models.DateTimeField(blank=True, null=True, db_index=True)
    remote_task_id = models.UUIDField(blank=True, null=True, db_index=True)
    cancel_requested_at = models.DateTimeField(blank=True, null=True, db_index=True)
    cancel_reason = models.CharField(max_length=500, blank=True, default="")
    execution_token = models.UUIDField(blank=True, null=True, db_index=True)
    execution_heartbeat_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "storage_repository_task"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["repository", "operation_type", "created_at"],
                name="stor_rt_repo_op_cr_idx",
            ),
            models.Index(
                fields=["execution_target", "operation_type", "created_at"],
                name="stor_rt_tgt_op_cr_idx",
            ),
            models.Index(
                fields=["triggered_by_task", "operation_type", "created_at"],
                name="stor_rt_trigger_op_cr_idx",
            ),
        ]


class RepositoryMaintenanceState(models.Model):
    """Persistent Quick/Full scheduling state for one physical repository."""

    execution_target = models.OneToOneField(
        RepositoryExecutionTarget,
        on_delete=models.CASCADE,
        related_name="maintenance_state",
    )
    last_quick_success_at = models.DateTimeField(blank=True, null=True)
    next_quick_due_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_full_success_at = models.DateTimeField(blank=True, null=True)
    next_full_due_at = models.DateTimeField(blank=True, null=True, db_index=True)
    last_failure_at = models.DateTimeField(blank=True, null=True)
    consecutive_failures = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(blank=True, null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "storage_repository_maintenance_state"
