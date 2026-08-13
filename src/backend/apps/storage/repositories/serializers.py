from __future__ import annotations

from rest_framework import serializers

from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.protection import conf as protection_conf
from apps.storage.repositories.models import Repository, RepositoryTask
from apps.task.models import Task
from apps.storage.selectors.interface import get_effective_storage_provider
from apps.storage.services.interface import create_repository, update_repository
from apps.storage.services.internal.repository_access import (
    explicit_repository_server_host,
    normalize_repository_server_host,
    repository_uses_bound_proxy,
)
from apps.storage.services.internal.repository_secrets import (
    credential_hint,
    sanitize_repository_config,
)
from apps.storage.services.internal.repository_endpoints import (
    S3_ENDPOINT_EXTERNAL,
    compact_s3_repository_endpoints,
    normalize_s3_endpoint_host,
    s3_endpoint_snapshot,
)
from apps.storage.services.internal.s3_url_style import normalize_s3_url_style


FORBIDDEN_CONFIG_FIELDS = {
    "bucket",
    "protocol",
    "nas_protocol",
    "bind_node_type",
    "bind_node_id",
    "repo_scope",
    "bind_node_name",
    "bind_node_ip",
    "proxy_node_id",
    "proxy_node_name",
    "proxy_node_ip",
    "mount_path",
    "repo_dir",
    "smb_server",
    "nfs_host",
    "nfs_export",
    "nfs_options",
    "endpoint_type",
    "external_endpoint",
    "internal_endpoint",
}

# These fields do not affect how an existing S3 repository is reached or
# authenticated. Updating them must not revalidate the repository's immutable
# location against today's Provider Catalog: a catalog entry can be renamed or
# removed after the repository was created while the saved endpoint remains
# valid.
S3_NON_CONNECTION_CONFIG_FIELDS = {
    "quota_gb",
    "quota_alert_enabled",
    "quota_alert_threshold",
}


# Fields that an S3 repository cannot change once it has been created.
# Enforced in RepositoryWriteSerializer.validate() for update/partial_update.
LOCKED_S3_UPDATE_FIELDS = (
    "s3_platform",
    "s3_bucket",
    "config.endpoint",
    "config.prefix",
)

def normalize_nas_share_path(value: object) -> str:
    path = str(value or "").strip().replace("\\", "/")
    path = "/" + path.lstrip("/")
    while "//" in path:
        path = path.replace("//", "/")
    return path.rstrip("/") or "/"


def mount_options_include_read_only(value: object) -> bool:
    return any(
        option.strip().lower() == "ro"
        for option in str(value or "").split(",")
    )


def normalize_s3_object_prefix(value: object) -> str:
    """Trim, strip leading slashes, collapse repeats, and append a trailing "/".

    Returns "" for empty/whitespace input.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    cleaned = raw.replace("\\", "/").lstrip("/").rstrip("/")
    if not cleaned:
        return ""
    while "//" in cleaned:
        cleaned = cleaned.replace("//", "/")
    return cleaned + "/"


class RepositorySerializer(serializers.ModelSerializer):
    config = serializers.SerializerMethodField()
    credential_hint = serializers.SerializerMethodField()
    bind_node_display_name = serializers.SerializerMethodField()
    bind_node_ip = serializers.SerializerMethodField()
    cross_proxy_access = serializers.SerializerMethodField()
    active_cleanup_task = serializers.SerializerMethodField()
    active_create_task = serializers.SerializerMethodField()

    class Meta:
        model = Repository
        fields = [
            "id",
            "organization_id",
            "name",
            "repo_type",
            "status",
            "health",
            "health_failures",
            "config",
            "credential_id",
            "credential_hint",
            "s3_platform",
            "s3_bucket",
            "s3_bucket_mode",
            "capacity_bytes",
            "estimated_usage_bytes",
            "physical_usage_bytes",
            "storage_total_bytes",
            "storage_used_bytes",
            "storage_available_bytes",
            "storage_pool_key",
            "storage_mount_point",
            "last_checked_at",
            "metrics_last_attempt_at",
            "usage_probe_status",
            "usage_last_success_at",
            "usage_last_error",
            "capacity_probe_status",
            "capacity_last_success_at",
            "capacity_last_error",
            "removed_at",
            "cleanup_result",
            "created_at",
            "updated_at",
            "nas_protocol",
            "bind_node_type",
            "bind_node_id",
            "bind_node_display_name",
            "bind_node_ip",
            "cross_proxy_access",
            "active_cleanup_task",
            "active_create_task",
        ]
        read_only_fields = fields

    def get_bind_node_display_name(self, obj: Repository) -> str | None:
        if not (obj.bind_node_id and obj.bind_node_type):
            return None
        node = Node.objects.filter(id=obj.bind_node_id).values_list("name", flat=True).first()
        return node

    def get_config(self, obj: Repository) -> dict:
        return sanitize_repository_config(obj.config if isinstance(obj.config, dict) else {})

    def get_credential_hint(self, obj: Repository) -> dict:
        return credential_hint(obj)

    def get_bind_node_ip(self, obj: Repository) -> str | None:
        if not (obj.bind_node_id and obj.bind_node_type):
            return None
        node = Node.objects.filter(id=obj.bind_node_id).values_list("ip_address", flat=True).first()
        return node

    def get_cross_proxy_access(self, obj: Repository) -> dict:
        if not repository_uses_bound_proxy(obj):
            return {"enabled": False, "ready": False, "host": None, "reason": "not_applicable"}
        if not protection_conf.PROTECTION_PROXY_REPOSITORY_SERVER_ENABLED:
            return {"enabled": False, "ready": False, "host": None, "reason": "feature_disabled"}
        node = Node.objects.filter(
            id=obj.bind_node_id,
            organization_id=obj.organization_id,
            role=NodeRole.PROXY,
            is_deleted=False,
        ).first()
        if node is None:
            return {"enabled": True, "ready": False, "host": None, "reason": "proxy_missing"}
        if node.availability != Node.Availability.ONLINE:
            return {"enabled": True, "ready": False, "host": None, "reason": "proxy_offline"}
        host, _source = explicit_repository_server_host(repository=obj, node=node)
        if not host:
            return {"enabled": True, "ready": False, "host": None, "reason": "host_missing"}
        return {"enabled": True, "ready": True, "host": host, "reason": "ready"}

    def get_active_cleanup_task(self, obj: Repository) -> dict | None:
        operation = (
            RepositoryTask.objects.filter(
                repository=obj,
                operation_type=RepositoryTask.OperationType.CLEANUP_REPOSITORY,
                task__status__in=(Task.Status.PENDING, Task.Status.RUNNING),
            )
            .select_related("task")
            .order_by("-created_at", "-id")
            .first()
        )
        if operation is None:
            return None
        task = operation.task
        return {
            "task_uuid": str(task.task_uuid),
            "status": task.status,
            "error_code": task.error_code,
            "error_message": task.error_message,
            "created_at": task.created_at,
        }

    def get_active_create_task(self, obj: Repository) -> dict | None:
        from apps.storage.services.internal.repository_create import (
            active_repository_create_task,
            repository_create_task_payload,
        )

        operation = active_repository_create_task(obj)
        if operation is None:
            return None
        return repository_create_task_payload(operation)


class RepositoryWriteSerializer(serializers.ModelSerializer):
    credential_payload = serializers.DictField(required=False, write_only=True)

    class Meta:
        model = Repository
        fields = [
            "name",
            "repo_type",
            "config",
            "credential_payload",
            "s3_platform",
            "s3_bucket",
            "s3_bucket_mode",
            "nas_protocol",
            "bind_node_type",
            "bind_node_id",
        ]
        extra_kwargs = {
            "config": {"required": False},
            "s3_platform": {"required": False, "allow_null": True},
            "s3_bucket": {"required": False, "allow_blank": True, "allow_null": True},
            "s3_bucket_mode": {"required": False},
            "nas_protocol": {"required": False, "allow_null": True},
            "bind_node_type": {"required": False, "allow_null": True},
            "bind_node_id": {"required": False, "allow_null": True},
        }

    def validate_config(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("config must be an object.")
        forbidden = sorted(FORBIDDEN_CONFIG_FIELDS & set(value))
        if forbidden:
            raise serializers.ValidationError(
                f"config contains forbidden fields: {', '.join(forbidden)}"
            )
        if "proxy_repository_server_host" in value:
            value = dict(value)
            try:
                value["proxy_repository_server_host"] = normalize_repository_server_host(
                    value.get("proxy_repository_server_host")
                )
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate(self, attrs):
        instance = self.instance
        incoming_config = attrs.get("config") or {}
        if instance is not None and self.context.get("request_action") in {
            "update",
            "partial_update",
        }:
            locked_errors: dict[str, str] = {}
            if "repo_type" in attrs and attrs["repo_type"] != instance.repo_type:
                locked_errors["repo_type"] = "Repository type cannot be modified."
            if "s3_platform" in attrs:
                locked_errors["s3_platform"] = "S3 platform cannot be modified."
            if "s3_bucket" in attrs:
                locked_errors["s3_bucket"] = "S3 bucket cannot be modified."
            if "s3_bucket_mode" in attrs:
                locked_errors["s3_bucket_mode"] = "S3 bucket mode cannot be modified."
            if isinstance(incoming_config, dict):
                if instance.repo_type == Repository.Type.S3:
                    for field in (
                        "endpoint",
                        "endpoint_type",
                        "external_endpoint",
                        "internal_endpoint",
                        "region",
                    ):
                        if field in incoming_config:
                            locked_errors[f"config.{field}"] = (
                                "S3 repository location cannot be modified."
                            )
                    if "prefix" in incoming_config:
                        locked_errors["config.prefix"] = (
                            "S3 object prefix cannot be modified."
                        )
                elif instance.repo_type == Repository.Type.NAS:
                    for field in ("server_address", "share_path"):
                        if field in incoming_config:
                            locked_errors[f"config.{field}"] = (
                                "NAS repository location cannot be modified."
                            )
                elif (
                    instance.repo_type == Repository.Type.PROXY_FS
                ):
                    for field in (
                        "proxy_node_base_dir",
                        "proxy_node_dir",
                        "proxy_fs_layout",
                    ):
                        if field in incoming_config:
                            locked_errors[f"config.{field}"] = (
                                "Proxy filesystem repository path cannot be modified."
                            )
            if instance.repo_type == Repository.Type.NAS and "nas_protocol" in attrs:
                locked_errors["nas_protocol"] = "NAS protocol cannot be modified."
            if locked_errors:
                raise serializers.ValidationError(locked_errors)

            if (
                instance.repo_type == Repository.Type.S3
                and set(attrs).issubset({"name", "config"})
                and set(incoming_config).issubset(S3_NON_CONNECTION_CONFIG_FIELDS)
                and not attrs.get("credential_payload")
            ):
                # Keep this as a partial config. update_repository() performs
                # the shallow merge, preserving the validated connection
                # snapshot from repository creation.
                return attrs

        repo_type = attrs.get("repo_type") or getattr(instance, "repo_type", None)
        config = {
            **(getattr(instance, "config", {}) or {}),
            **(attrs.get("config") or {}),
        }
        credential_payload = attrs.get("credential_payload") or {}
        s3_platform = attrs.get("s3_platform", getattr(instance, "s3_platform", None))
        s3_bucket = attrs.get("s3_bucket", getattr(instance, "s3_bucket", None))
        nas_protocol = attrs.get("nas_protocol", getattr(instance, "nas_protocol", None))
        bind_node_type = attrs.get("bind_node_type", getattr(instance, "bind_node_type", None))
        bind_node_id = attrs.get("bind_node_id", getattr(instance, "bind_node_id", None))

        if repo_type not in {choice[0] for choice in Repository.Type.choices}:
            raise serializers.ValidationError({"repo_type": "Unsupported repository type."})

        if repo_type == Repository.Type.S3:
            if instance is None and "s3_bucket_mode" not in attrs:
                raise serializers.ValidationError(
                    {"s3_bucket_mode": "S3 bucket mode is required."}
                )
            s3_platform = str(s3_platform or "").strip().lower()
            if "s3_platform" in attrs:
                attrs["s3_platform"] = s3_platform
            self._validate_s3(config, credential_payload, s3_platform, s3_bucket)
        elif repo_type == Repository.Type.NAS:
            self._validate_nas(config, credential_payload, nas_protocol, bind_node_type, bind_node_id)
        elif repo_type == Repository.Type.PROXY_FS:
            self._validate_proxy_fs(config, bind_node_type, bind_node_id)

        if repo_type != Repository.Type.S3 and "s3_bucket_mode" in attrs:
            raise serializers.ValidationError(
                {"s3_bucket_mode": "S3 bucket mode is only accepted for S3 repositories."}
            )

        attrs["config"] = config
        return attrs

    def _validate_s3(self, config, credential_payload, s3_platform, s3_bucket) -> None:
        s3_platform = str(s3_platform or "").strip().lower()
        if not s3_platform:
            raise serializers.ValidationError({"s3_platform": "S3 platform is required."})
        if not str(s3_bucket or "").strip():
            raise serializers.ValidationError({"s3_bucket": "S3 bucket is required."})

        if s3_platform != Repository.S3Platform.CUSTOM:
            provider_record = get_effective_storage_provider(str(s3_platform or ""))
            provider = provider_record.get("config") if provider_record else None
            if not provider or not provider.get("enabled"):
                raise serializers.ValidationError(
                    {"s3_platform": "Managed S3 Provider is not enabled in the current Catalog."}
                )
            region_id = str(config.get("region") or "").strip()
            region = next(
                (
                    item
                    for item in provider.get("regions", [])
                    if item.get("id") == region_id
                ),
                None,
            )
            if region is None:
                raise serializers.ValidationError(
                    {"config.region": "Region is not in the current Provider Catalog."}
                )
            try:
                snapshot = s3_endpoint_snapshot(
                    external_endpoint=region.get("external_endpoint"),
                    internal_endpoint=region.get("internal_endpoint"),
                    endpoint_type=S3_ENDPOINT_EXTERNAL,
                )
            except ValueError as exc:
                raise serializers.ValidationError(
                    {"config.endpoint": str(exc)}
                ) from exc
            submitted_endpoint = normalize_s3_endpoint_host(config.get("endpoint"))
            if submitted_endpoint and submitted_endpoint != snapshot["endpoint"]:
                raise serializers.ValidationError(
                    {"config.endpoint": "Endpoint does not match the current Provider Catalog region."}
                )
            compacted = compact_s3_repository_endpoints(
                config,
                s3_platform=s3_platform,
                external_endpoint=snapshot["external_endpoint"],
                internal_endpoint=snapshot["internal_endpoint"],
            )
            config.clear()
            config.update(compacted)
            config["s3_url_style"] = region["s3_url_style"]
            config["use_tls"] = region["use_tls"]
        else:
            endpoint = normalize_s3_endpoint_host(config.get("endpoint"))
            try:
                compacted = compact_s3_repository_endpoints(
                    config,
                    s3_platform=s3_platform,
                    external_endpoint=endpoint,
                    internal_endpoint=endpoint,
                )
            except ValueError as exc:
                raise serializers.ValidationError(
                    {"config.endpoint": str(exc)}
                ) from exc
            config.clear()
            config.update(compacted)

        instance = self.instance
        if str(credential_payload.get("access_key_id") or "").strip():
            config["access_key_id"] = str(credential_payload.get("access_key_id") or "").strip()
        if instance is not None and instance.repo_type == Repository.Type.S3:
            existing_config = instance.config or {}
            # On update, callers may omit credentials to keep the existing ones.
            if not str(config.get("access_key_id") or "").strip():
                config["access_key_id"] = existing_config.get("access_key_id", "")

        if not str(config.get("access_key_id") or "").strip():
            raise serializers.ValidationError({"config.access_key_id": "Access key ID is required."})
        has_secret = bool(str(config.get("secret_access_key") or "").strip() or str(credential_payload.get("secret_access_key") or "").strip())
        if instance is not None and getattr(instance, "credential_id", None):
            has_secret = True
        if instance is not None and str((instance.config or {}).get("secret_access_key") or "").strip():
            has_secret = True
        if not has_secret:
            raise serializers.ValidationError({"config.secret_access_key": "Secret access key is required."})

        config["prefix"] = normalize_s3_object_prefix(config.get("prefix"))
        try:
            config["s3_url_style"] = normalize_s3_url_style(
                config.get("s3_url_style"), platform=s3_platform
            )
        except ValueError as exc:
            raise serializers.ValidationError({"config.s3_url_style": str(exc)}) from exc
        if instance is None and not config["prefix"]:
            raise serializers.ValidationError({"config.prefix": "S3 object prefix is required."})

    def _validate_nas(
        self,
        config,
        credential_payload,
        nas_protocol,
        bind_node_type,
        bind_node_id,
    ) -> None:
        instance = self.instance
        if nas_protocol not in {choice[0] for choice in Repository.NasProtocol.choices}:
            raise serializers.ValidationError({"nas_protocol": "NAS protocol is required."})
        if not str(config.get("server_address") or "").strip():
            raise serializers.ValidationError({"config.server_address": "Server address is required."})
        if not str(config.get("share_path") or "").strip():
            raise serializers.ValidationError({"config.share_path": "Share path is required."})
        config["share_path"] = normalize_nas_share_path(config.get("share_path"))
        if bool(bind_node_type) != bool(bind_node_id):
            raise serializers.ValidationError({"bind_node_id": "Bind node type and ID must be set together."})
        if bind_node_type and bind_node_type != Repository.BindNodeType.PROXY:
            raise serializers.ValidationError({"bind_node_type": "Only proxy bind nodes are supported."})
        if bind_node_id:
            self._validate_proxy_node_target(bind_node_id, instance=instance)
        # Already-bound NAS repositories cannot unbind the Proxy.
        if (
            instance is not None
            and instance.bind_node_type == Repository.BindNodeType.PROXY
            and instance.bind_node_id
            and not bind_node_id
        ):
            raise serializers.ValidationError(
                {"bind_node_id": "Cannot unbind proxy. Replace the proxy instead."}
            )

        if nas_protocol == Repository.NasProtocol.SMB:
            if mount_options_include_read_only(config.get("mount_options")):
                raise serializers.ValidationError(
                    {
                        "config.mount_options": (
                            "SMB backup repositories cannot use the read-only (ro) mount option."
                        )
                    }
                )
            if str(credential_payload.get("smb_username") or "").strip():
                config["smb_username"] = str(credential_payload.get("smb_username") or "").strip()
            if str(credential_payload.get("smb_domain") or "").strip():
                config["smb_domain"] = str(credential_payload.get("smb_domain") or "").strip()
            if not str(config.get("smb_username") or "").strip():
                raise serializers.ValidationError({"config.smb_username": "SMB username is required."})
            has_password = bool(str(config.get("smb_password") or "").strip() or str(credential_payload.get("smb_password") or "").strip())
            if instance is not None and getattr(instance, "credential_id", None):
                has_password = True
            if instance is not None and str((instance.config or {}).get("smb_password") or "").strip():
                has_password = True
            if not has_password:
                raise serializers.ValidationError({"config.smb_password": "SMB password is required."})
        else:
            for key in ("smb_username", "smb_password", "smb_domain"):
                if key in config or key in credential_payload:
                    raise serializers.ValidationError({f"config.{key}": "SMB fields are not accepted for NFS."})

    def _validate_proxy_fs(self, config, bind_node_type, bind_node_id) -> None:
        instance = self.instance
        # Standalone-disk repositories must always stay bound to a Proxy.
        # Existing rows can replace the Proxy, but cannot unbind it.
        if not bind_node_id and instance is not None and instance.bind_node_id:
            raise serializers.ValidationError(
                {"bind_node_id": "Cannot unbind proxy. Replace the proxy instead."}
            )
        if not bind_node_id:
            raise serializers.ValidationError({"bind_node_id": "Proxy filesystem must bind a proxy node."})
        if bind_node_type and bind_node_type != Repository.BindNodeType.PROXY:
            raise serializers.ValidationError({"bind_node_type": "Proxy filesystem must bind a proxy node."})
        self._validate_proxy_node_target(bind_node_id, instance=instance)
        base_dir = str(
            config.get("proxy_node_base_dir") or config.get("proxy_node_dir") or ""
        ).strip()
        if not base_dir:
            raise serializers.ValidationError(
                {"config.proxy_node_base_dir": "Proxy node base directory is required."}
            )
        if self.instance is None:
            config["proxy_node_base_dir"] = base_dir

    def _validate_proxy_node_target(self, bind_node_id, *, instance) -> None:
        organization_id = self.context.get("organization_id")
        if organization_id is None:
            return
        node = Node.objects.filter(
            id=bind_node_id,
            organization_id=organization_id,
            role=NodeRole.PROXY,
            is_deleted=False,
        ).first()
        if node is None:
            raise serializers.ValidationError({"bind_node_id": "Bound proxy node not found in this organization."})

    def create(self, validated_data):
        organization_id = self.context["organization_id"]
        request = self.context.get("request")
        requested_by = getattr(request, "user", None) if request is not None else None
        return create_repository(
            organization_id=organization_id,
            name=validated_data["name"],
            repo_type=validated_data["repo_type"],
            config=validated_data.get("config") or {},
            s3_platform=validated_data.get("s3_platform"),
            s3_bucket=validated_data.get("s3_bucket"),
            s3_bucket_mode=validated_data.get("s3_bucket_mode"),
            nas_protocol=validated_data.get("nas_protocol"),
            bind_node_type=validated_data.get("bind_node_type"),
            bind_node_id=validated_data.get("bind_node_id"),
            credential_payload=validated_data.get("credential_payload") or {},
            requested_by=requested_by,
        )

    def update(self, instance, validated_data):
        credential_payload = validated_data.get("credential_payload")
        incoming_config = validated_data.get("config")
        if credential_payload is None and isinstance(incoming_config, dict):
            if any(key in incoming_config for key in ("secret_access_key", "smb_password", "kopia_password")):
                credential_payload = {}
        return update_repository(
            repository=instance,
            name=validated_data.get("name"),
            config=validated_data.get("config"),
            s3_platform=validated_data.get("s3_platform"),
            s3_bucket=validated_data.get("s3_bucket"),
            nas_protocol=validated_data.get("nas_protocol"),
            bind_node_type=validated_data.get("bind_node_type"),
            bind_node_id=validated_data.get("bind_node_id"),
            credential_payload=credential_payload,
        )


class RepositoryCleanupSerializer(serializers.Serializer):
    force = serializers.BooleanField(required=False, default=False)
    confirmation = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs["force"] and attrs["confirmation"] != "FORCE CLEANUP":
            raise serializers.ValidationError(
                {"confirmation": 'Type "FORCE CLEANUP" exactly to confirm.'}
            )
        return attrs


class RepositoryCleanupPreflightSerializer(serializers.Serializer):
    force = serializers.BooleanField(required=False, default=False)


class NASRepositoryRepairSerializer(serializers.Serializer):
    """Validates the payload for ``PATCH /repositories/{id}/repair/``.

    The endpoint is intentionally narrower than the full write serializer:
    we only accept fields the repair UI can actually change, and we do not
    let callers mutate structural fields (type, host, share_path, ...).
    """

    name = serializers.CharField(required=False, allow_blank=False, max_length=200, trim_whitespace=True)
    bind_node_id = serializers.IntegerField(required=False, allow_null=True)

    # NAS mutable config fields. The "config" wrapper is reused so the
    # frontend keeps a single payload shape with create/update.
    config = serializers.DictField(required=False)

    def validate_config(self, value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("config must be an object.")
        forbidden = sorted(FORBIDDEN_CONFIG_FIELDS & set(value))
        if forbidden:
            raise serializers.ValidationError(
                "config contains forbidden fields: %s" % ", ".join(forbidden)
            )
        if "proxy_repository_server_host" in value:
            value = dict(value)
            try:
                value["proxy_repository_server_host"] = normalize_repository_server_host(
                    value.get("proxy_repository_server_host")
                )
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return value

    def validate(self, attrs):
        instance = self.context.get("repository")
        if instance is None or instance.repo_type != Repository.Type.NAS:
            raise serializers.ValidationError(
                {"detail": "Repair is only supported for NAS repositories."}
            )
        config = attrs.get("config") or {}
        protocol = instance.nas_protocol
        if protocol == Repository.NasProtocol.NFS:
            for key in ("smb_username", "smb_password", "smb_domain"):
                if key in config:
                    raise serializers.ValidationError(
                        {"config.%s" % key: "SMB fields are not accepted for NFS."}
                    )
        return attrs
