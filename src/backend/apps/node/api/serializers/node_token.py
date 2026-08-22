"""Serializers for ``NodeToken``."""

from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers

from apps.node import conf as node_conf
from apps.node.models import Node, NodeInstallationMode, NodeToken
from common.deploy.site import enrollment_tls_verify


class NodeTokenSerializer(serializers.ModelSerializer):
    token = serializers.SerializerMethodField()
    tls_verify = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

    class Meta:
        model = NodeToken
        fields = [
            "id",
            "organization",
            "token",
            "role",
            "installation_mode",
            "note",
            "is_active",
            "created_at",
            "updated_at",
            "expires_at",
            "used_at",
            "status",
            "gateway_scope",
            "tls_verify",
            "is_deleted",
            "deleted_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "token",
            "role",
            "installation_mode",
            "created_at",
            "updated_at",
            "expires_at",
            "used_at",
            "status",
            "gateway_scope",
            "is_deleted",
            "deleted_at",
        ]

    def get_tls_verify(self, _instance: NodeToken) -> bool:
        """Return the deployment TLS policy used by generated install commands."""
        return enrollment_tls_verify()

    def get_token(self, instance: NodeToken) -> str:
        """Expose the capability only in the immediate create response."""
        return instance.token if self.context.get("include_token") is True else ""

    def get_status(self, instance: NodeToken) -> str:
        if not instance.is_active:
            return "revoked"
        if instance.expires_at and instance.expires_at <= timezone.now():
            return "expired"
        return "active"


class NodeTokenCreateSerializer(serializers.ModelSerializer):
    """Create enrollment token (organization from active ``X-Org-Key`` / ``?org=``)."""

    org = serializers.SlugField(required=False, write_only=True)

    class Meta:
        model = NodeToken
        fields = [
            "org",
            "role",
            "installation_mode",
            "note",
            "expires_at",
            "is_active",
            "gateway_scope",
        ]
        read_only_fields = ["gateway_scope"]
        extra_kwargs = {
            "note": {"required": False, "default": ""},
            "is_active": {"required": False, "default": True},
            "expires_at": {"required": False},
        }

    def validate_role(self, value: str) -> str:
        if value not in dict(Node.Role.choices):
            raise serializers.ValidationError("invalid role")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        role = attrs.get("role")
        installation_mode = attrs.get(
            "installation_mode",
            NodeInstallationMode.SYSTEM,
        )
        if installation_mode in (
            NodeInstallationMode.USER,
            NodeInstallationMode.ACCOUNT,
        ) and role != Node.Role.AGENT:
            raise serializers.ValidationError(
                {
                    "installation_mode": (
                        "User-scoped installation is only available for Source Agent."
                    )
                }
            )
        return attrs

    def create(self, validated_data):
        ttl = max(1, node_conf.ENROLLMENT_TOKEN_TTL_SECONDS)
        validated_data["expires_at"] = timezone.now() + timedelta(seconds=ttl)
        validated_data["enrollment_mode"] = NodeToken.EnrollmentMode.CURRENT
        return super().create(validated_data)
