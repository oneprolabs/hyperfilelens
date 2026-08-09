from rest_framework import serializers

from apps.subscription.models import License, LicenseHistory, MachineCode


class LicenseSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    organization_key = serializers.CharField(source="organization.key", read_only=True)
    is_valid = serializers.BooleanField(read_only=True)
    is_perpetual = serializers.BooleanField(read_only=True)
    days_until_expiry = serializers.IntegerField(read_only=True)

    class Meta:
        model = License
        fields = [
            "id",
            "license_key",
            "version",
            "change_type",
            "change_reason",
            "machine_code",
            "organization",
            "organization_name",
            "organization_key",
            "max_organizations",
            "max_users",
            "max_nodes",
            "max_storage_gb",
            "max_gateways",
            "max_public_gateways",
            "max_public_gateway_capacity_gb",
            "ai_insights_quota",
            "max_tasks",
            "max_alert_policies",
            "issued_at",
            "expires_at",
            "activated_at",
            "updated_at",
            "status",
            "is_valid",
            "is_perpetual",
            "days_until_expiry",
        ]
        read_only_fields = fields


class LicenseHistorySerializer(serializers.ModelSerializer):
    is_perpetual = serializers.SerializerMethodField()
    limits = serializers.SerializerMethodField()

    class Meta:
        model = LicenseHistory
        fields = [
            "id",
            "license_key",
            "version",
            "machine_code",
            "organization",
            "limits",
            "max_organizations",
            "max_users",
            "max_nodes",
            "max_storage_gb",
            "max_gateways",
            "max_public_gateways",
            "max_public_gateway_capacity_gb",
            "ai_insights_quota",
            "max_tasks",
            "max_alert_policies",
            "issued_at",
            "expires_at",
            "activated_at",
            "archived_at",
            "status",
            "is_perpetual",
            "change_type",
            "change_reason",
        ]
        read_only_fields = fields

    def get_is_perpetual(self, obj):
        return obj.expires_at is None

    def get_limits(self, obj):
        return {
            "max_organizations": obj.max_organizations,
            "max_users": obj.max_users,
            "max_nodes": obj.max_nodes,
            "max_storage_gb": obj.max_storage_gb,
            "max_gateways": obj.max_gateways,
            "max_public_gateways": obj.max_public_gateways,
            "max_public_gateway_capacity_gb": obj.max_public_gateway_capacity_gb,
            "ai_insights_quota": obj.ai_insights_quota,
            "ai_tokens": obj.ai_insights_quota,
            "max_tasks": obj.max_tasks,
            "max_alert_policies": obj.max_alert_policies,
        }


class MachineCodeSerializer(serializers.ModelSerializer):
    organization_name = serializers.CharField(source="organization.name", read_only=True)

    class Meta:
        model = MachineCode
        fields = ["code", "organization", "organization_name", "hostname", "source", "created_at"]
        read_only_fields = fields


class ActivateLicenseSerializer(serializers.Serializer):
    activation_code = serializers.CharField(max_length=4096)
