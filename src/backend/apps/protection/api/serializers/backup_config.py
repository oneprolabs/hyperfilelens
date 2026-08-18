from __future__ import annotations

from rest_framework import serializers

from apps.protection.models import BackupConfig, BackupConfigDirectory
from apps.restore.models import RestorePlan


class BackupConfigDirectorySerializer(serializers.ModelSerializer):
    class Meta:
        model = BackupConfigDirectory
        fields = [
            "id",
            "path",
            "path_type",
            "display_name",
            "estimated_size_bytes",
            "sort_order",
        ]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["estimated_size_bytes"] = max(0, int(data.get("estimated_size_bytes") or 0))
        return data


class BackupConfigListSerializer(serializers.ModelSerializer):
    directory_count = serializers.SerializerMethodField()

    class Meta:
        model = BackupConfig
        fields = [
            "id",
            "name",
            "source_type",
            "source_ref_id",
            "repository_id",
            "repository_endpoint_type",
            "backup_policy_id",
            "file_filter_rule_id",
            "directory_count",
            "compression_level",
            "status",
            "reset_task_uuid",
            "provisioning_task_uuid",
            "provisioning_error_code",
            "provisioning_error_message",
            "recovery_plan_enabled",
            "created_at",
            "updated_at",
        ]

    def get_directory_count(self, obj: BackupConfig) -> int:
        return getattr(obj, "_directory_count", 0)


class BackupConfigDetailSerializer(serializers.ModelSerializer):
    directories = BackupConfigDirectorySerializer(many=True, read_only=True)
    directory_count = serializers.SerializerMethodField()
    recovery_plans = serializers.SerializerMethodField()

    class Meta:
        model = BackupConfig
        fields = [
            "id",
            "name",
            "remark",
            "source_type",
            "source_ref_id",
            "repository_id",
            "repository_endpoint_type",
            "backup_policy_id",
            "file_filter_rule_id",
            "directory_count",
            "compression_level",
            "status",
            "reset_task_uuid",
            "provisioning_task_uuid",
            "provisioning_error_code",
            "provisioning_error_message",
            "recovery_plan_enabled",
            "directories",
            "recovery_plans",
            "created_at",
            "updated_at",
        ]

    def get_directory_count(self, obj: BackupConfig) -> int:
        return obj.directories.count()

    def get_recovery_plans(self, obj: BackupConfig) -> list[dict]:
        plans = RestorePlan.objects.filter(
            organization_id=obj.organization_id,
            backup_config_id=obj.id,
        )
        return [
            {
                "id": plan.id,
                "source_type": plan.source_type,
                "source_ref_id": plan.source_ref_id,
                "scope": plan.scope,
                "backup_config_dir_id": plan.backup_config_dir_id,
                "source_path": plan.source_path,
                "target_type": plan.target_type,
                "target_ref_id": plan.target_ref_id,
                "restore_host_id": plan.target_ref_id if plan.target_type == "agent" else None,
                "restore_dir": plan.restore_dir,
                "conflict_mode": plan.conflict_mode,
                "enabled": plan.enabled,
                "sort_order": plan.sort_order,
            }
            for plan in plans
        ]


class BackupConfigWriteSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=False)
    remark = serializers.CharField(required=False, allow_blank=True)
    source_type = serializers.CharField(max_length=16, required=False)
    source_ref_id = serializers.IntegerField(required=False)
    repository_id = serializers.IntegerField(required=False)
    repository_endpoint_type = serializers.ChoiceField(
        choices=BackupConfig.RepositoryEndpointType.choices,
        required=False,
    )
    backup_policy_id = serializers.IntegerField(required=False, allow_null=True)
    file_filter_rule_id = serializers.IntegerField(required=False, allow_null=True)
    compression_level = serializers.ChoiceField(
        choices=BackupConfig.CompressionLevel.choices,
        required=False,
    )
    recovery_plan_enabled = serializers.BooleanField(required=False)
    directories = serializers.ListField(required=False)
    recovery_plans = serializers.ListField(required=False)

    def validate(self, attrs):
        if not self.partial:
            required = ("name", "source_type", "source_ref_id", "repository_id", "directories")
            missing = [field for field in required if field not in attrs]
            if missing:
                raise serializers.ValidationError(
                    {field: "This field is required." for field in missing}
                )
        return attrs


class BackupConfigResetSerializer(serializers.Serializer):
    source_ids = serializers.ListField(
        child=serializers.CharField(max_length=80),
        allow_empty=False,
    )
    confirmation = serializers.CharField(max_length=16, trim_whitespace=False)
