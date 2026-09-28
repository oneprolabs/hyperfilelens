"""Serializers for ``NodeTask``."""

from rest_framework import serializers

from apps.node.models import NodeTask
from apps.task.error_contract import node_task_error_contract
from apps.storage.services.internal.repository_secrets import scrub_secrets


class NodeTaskSerializer(serializers.ModelSerializer):
    payload = serializers.SerializerMethodField()
    result = serializers.SerializerMethodField()
    last_error = serializers.SerializerMethodField()
    error_details = serializers.SerializerMethodField()

    class Meta:
        model = NodeTask
        fields = [
            "id",
            "organization",
            "node",
            "kind",
            "payload",
            "result",
            "status",
            "correlation_type",
            "correlation_id",
            "dispatched_at",
            "last_progress_at",
            "watchdog_deadline_at",
            "last_error",
            "error_details",
            "created_at",
            "updated_at",
            "is_deleted",
            "deleted_at",
        ]
        read_only_fields = fields

    def get_payload(self, obj: NodeTask):
        return scrub_secrets(obj.payload)

    def get_result(self, obj: NodeTask):
        return scrub_secrets(obj.result)

    def get_last_error(self, obj: NodeTask) -> str:
        return str(scrub_secrets(obj.last_error or ""))

    def get_error_details(self, obj: NodeTask):
        return node_task_error_contract(obj)


class NodeTaskDispatchSerializer(serializers.Serializer):
    """Dispatch a runtime task to an Agent (maps to ``NodeTask`` create)."""

    org_key = serializers.SlugField(required=False)
    node_id = serializers.IntegerField()
    kind = serializers.CharField(max_length=120)
    payload = serializers.JSONField(required=False, default=dict)
    correlation_type = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
    correlation_id = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
    )
