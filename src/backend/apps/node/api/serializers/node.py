"""Serializers for ``Node``."""

from rest_framework import serializers

from apps.node.models import Node
from apps.node.services.internal.node_registry import agent_ws_routable
from apps.node.services.internal.repository_server import (
    normalize_repository_server_host,
)


class NodeSerializer(serializers.ModelSerializer):
    """Console REST representation of a registered Agent node."""

    agent_control_ws_path = serializers.SerializerMethodField(read_only=True)
    routable = serializers.SerializerMethodField()
    lifecycle = serializers.SerializerMethodField(read_only=True)
    workload = serializers.SerializerMethodField(read_only=True)
    effective_repository_server_address = serializers.SerializerMethodField(
        read_only=True
    )
    repository_server_address_source = serializers.SerializerMethodField(
        read_only=True
    )

    class Meta:
        model = Node
        fields = [
            "id",
            "organization",
            "name",
            "role",
            "version",
            "os_name",
            "ip_address",
            "repository_server_address",
            "effective_repository_server_address",
            "repository_server_address_source",
            "status",
            "availability",
            "availability_updated_at",
            "routable",
            "last_seen_at",
            "metadata",
            "created_at",
            "updated_at",
            "is_deleted",
            "deleted_at",
            "agent_control_ws_path",
            "lifecycle",
            "workload",
        ]
        read_only_fields = [
            "id",
            "ip_address",
            "effective_repository_server_address",
            "repository_server_address_source",
            "availability",
            "availability_updated_at",
            "created_at",
            "updated_at",
            "is_deleted",
            "deleted_at",
            "agent_control_ws_path",
            "lifecycle",
            "workload",
        ]

    @staticmethod
    def get_agent_control_ws_path(_obj: Node) -> str:
        return "/ws/node/agent/"

    @staticmethod
    def get_routable(obj: Node) -> bool:
        if obj.role not in (Node.Role.AGENT, Node.Role.PROXY, Node.Role.GATEWAY):
            return obj.availability == Node.Availability.ONLINE
        return agent_ws_routable(agent_id=obj.id)

    def get_lifecycle(self, obj: Node):
        enrichments = self.context.get("enrichments") or {}
        row = enrichments.get(obj.id)
        if isinstance(row, dict):
            return row.get("lifecycle")
        return None

    def get_workload(self, obj: Node):
        enrichments = self.context.get("enrichments") or {}
        row = enrichments.get(obj.id)
        if isinstance(row, dict):
            return row.get("workload")
        return None

    @staticmethod
    def get_effective_repository_server_address(obj: Node) -> str | None:
        override = str(obj.repository_server_address or "").strip()
        return override or (str(obj.ip_address) if obj.ip_address else None)

    @staticmethod
    def get_repository_server_address_source(obj: Node) -> str:
        if str(obj.repository_server_address or "").strip():
            return "proxy_override"
        if obj.ip_address:
            return "agent_reported"
        return "unavailable"

    def validate_repository_server_address(self, value: object) -> str:
        try:
            return normalize_repository_server_host(value)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def validate(self, attrs):
        instance = self.instance
        role = attrs.get("role", getattr(instance, "role", None))
        if "repository_server_address" in attrs and role != Node.Role.PROXY:
            raise serializers.ValidationError(
                {
                    "repository_server_address": (
                        "Repository Server Address can only be configured for a Proxy Host."
                    )
                }
            )
        return attrs


class NodeHeartbeatSerializer(serializers.Serializer):
    """Agent HTTP heartbeat payload (``NodeViewSet.heartbeat``)."""

    node_id = serializers.IntegerField(required=False)
    installation_id = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=128,
    )
    existing_node_credential = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=256,
        write_only=True,
    )
    name = serializers.CharField(required=False, allow_blank=True)
    role = serializers.ChoiceField(choices=Node.Role.choices)
    version = serializers.CharField(required=False, allow_blank=True)
    os_name = serializers.CharField(required=False, allow_blank=True)
    metadata = serializers.JSONField(required=False)
