from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from apps.task.models import Task, TaskDependency, TaskEvent, TaskResource, TaskStep
from apps.task.services.interface import create_task


class TaskResourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskResource
        fields = ["resource_type", "resource_subtype", "resource_id", "is_primary"]


class TaskStepSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskStep
        fields = ["id", "step_index", "step_name", "status", "progress", "created_at"]
        read_only_fields = fields


class TaskEventSerializer(serializers.ModelSerializer):
    step_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = TaskEvent
        fields = ["id", "step_id", "seq", "level", "message", "metadata", "created_at"]
        read_only_fields = fields


class TaskDependencySerializer(serializers.ModelSerializer):
    blocking_task_uuid = serializers.SerializerMethodField()
    blocking_task_status = serializers.SerializerMethodField()

    class Meta:
        model = TaskDependency
        fields = [
            "id",
            "blocking_task_uuid",
            "blocking_task_status",
            "reference_type",
            "reference_id",
            "reference_task_type",
            "code",
            "detail",
            "auto_resumable",
            "is_active",
            "created_at",
            "last_checked_at",
            "next_check_at",
            "resolved_at",
        ]
        read_only_fields = fields

    def get_blocking_task_uuid(self, obj: TaskDependency) -> str | None:
        return str(obj.blocking_task.task_uuid) if obj.blocking_task_id else None

    def get_blocking_task_status(self, obj: TaskDependency) -> str | None:
        return obj.blocking_task.status if obj.blocking_task_id else None


class TaskSerializer(serializers.ModelSerializer):
    resources = TaskResourceSerializer(many=True, read_only=True)
    steps = TaskStepSerializer(many=True, read_only=True)
    recent_events = serializers.SerializerMethodField()
    primary_resource = serializers.SerializerMethodField()
    transfer_progress = serializers.SerializerMethodField()
    operation_type = serializers.SerializerMethodField()
    repository_owner = serializers.SerializerMethodField()
    repository_target = serializers.SerializerMethodField()
    repository_cleanup = serializers.SerializerMethodField()
    repository_cancellation = serializers.SerializerMethodField()
    replaces_task_uuid = serializers.SerializerMethodField()
    replacement_task_uuid = serializers.SerializerMethodField()
    dependencies = TaskDependencySerializer(many=True, read_only=True)
    actions = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            "id",
            "organization_id",
            "task_uuid",
            "task_type",
            "display_name",
            "status",
            "progress",
            "current_step",
            "retry_count",
            "recovery_attempt",
            "replaces_task_uuid",
            "replacement_task_uuid",
            "trigger_type",
            "request_payload",
            "group_uuid",
            "result_payload",
            "transfer_progress",
            "operation_type",
            "repository_owner",
            "repository_target",
            "repository_cleanup",
            "repository_cancellation",
            "error_code",
            "error_message",
            "started_at",
            "finished_at",
            "created_at",
            "updated_at",
            "resources",
            "primary_resource",
            "steps",
            "recent_events",
            "dependencies",
            "actions",
        ]
        read_only_fields = fields

    def get_replaces_task_uuid(self, obj: Task) -> str | None:
        return str(obj.replaces_task.task_uuid) if obj.replaces_task_id else None

    def get_replacement_task_uuid(self, obj: Task) -> str | None:
        try:
            replacement = obj.replacement_task
        except ObjectDoesNotExist:
            return None
        return str(replacement.task_uuid)

    def get_actions(self, obj: Task) -> dict[str, bool]:
        waiting = obj.status in {Task.Status.WAITING, Task.Status.BLOCKED}
        return {
            "can_cancel": waiting if obj.task_type == Task.Type.SOURCE_UNREGISTER else (
                obj.status in {Task.Status.PENDING, Task.Status.RUNNING}
                and obj.task_type not in {
                    Task.Type.NODE_LIFECYCLE,
                    Task.Type.REPOSITORY_OPERATION,
                }
            ),
            "can_recheck": obj.task_type == Task.Type.SOURCE_UNREGISTER and waiting,
            "can_retry": (
                obj.task_type not in {
                    Task.Type.NODE_LIFECYCLE,
                    Task.Type.REPOSITORY_OPERATION,
                }
                and obj.status
                in {Task.Status.FAILED, Task.Status.TIMEOUT, Task.Status.CANCELLED}
            ),
            "can_force": False,
        }

    def get_transfer_progress(self, obj: Task) -> dict | None:
        payload = obj.result_payload if isinstance(obj.result_payload, dict) else {}
        transfer = payload.get("transfer_progress")
        return transfer if isinstance(transfer, dict) else None

    def get_recent_events(self, obj: Task) -> list[dict]:
        events = obj.events.order_by("-seq", "-id")[:5]
        return TaskEventSerializer(list(reversed(list(events))), many=True).data

    def get_primary_resource(self, obj: Task) -> dict | None:
        resource = next((item for item in obj.resources.all() if item.is_primary), None)
        return TaskResourceSerializer(resource).data if resource else None

    def _repository_operation(self, obj: Task):
        if obj.task_type != Task.Type.REPOSITORY_OPERATION:
            return None
        try:
            return obj.repository_operation
        except ObjectDoesNotExist:
            return None

    def get_operation_type(self, obj: Task) -> str | None:
        operation = self._repository_operation(obj)
        return operation.operation_type if operation else None

    def get_repository_owner(self, obj: Task) -> dict | None:
        operation = self._repository_operation(obj)
        if operation is None:
            return None
        return {
            "type": operation.owner_type,
            "node_id": operation.owner_node_id,
            "identity": operation.owner_identity,
        }

    def get_repository_target(self, obj: Task) -> dict | None:
        operation = self._repository_operation(obj)
        if operation is None or operation.execution_target_id is None:
            return None
        return {
            "id": operation.execution_target_id,
            "key": operation.execution_target.target_key,
            "repository_subdir": operation.execution_target.repository_subdir,
        }

    def get_repository_cleanup(self, obj: Task) -> dict | None:
        operation = self._repository_operation(obj)
        if operation is None or operation.operation_type not in {
            "cleanup.target",
            "cleanup.repository",
        }:
            return None
        return {
            "force": operation.force,
            "triggered_by_task_uuid": (
                str(operation.triggered_by_task.task_uuid)
                if operation.triggered_by_task_id
                else None
            ),
            "triggered_by_task_type": (
                operation.triggered_by_task.task_type
                if operation.triggered_by_task_id
                else None
            ),
        }

    def get_repository_cancellation(self, obj: Task) -> dict | None:
        operation = self._repository_operation(obj)
        if operation is None:
            return None
        supported = operation.owner_type == "controller" and operation.operation_type in {
            "maintenance.quick",
            "maintenance.full",
        }
        return {
            "supported": supported,
            "requested_at": (
                operation.cancel_requested_at.isoformat()
                if operation.cancel_requested_at is not None
                else None
            ),
        }


class TaskResourceInputSerializer(serializers.Serializer):
    resource_type = serializers.ChoiceField(choices=TaskResource.Type.choices)
    resource_subtype = serializers.CharField(required=False, allow_blank=True, max_length=32)
    resource_id = serializers.IntegerField(min_value=1)
    is_primary = serializers.BooleanField(required=False, default=False)


class TaskStepInputSerializer(serializers.Serializer):
    step_name = serializers.CharField(max_length=64)
    status = serializers.ChoiceField(
        choices=TaskStep.Status.choices,
        required=False,
        default=TaskStep.Status.PENDING,
    )
    progress = serializers.DecimalField(
        max_digits=5,
        decimal_places=2,
        min_value=Decimal("0.00"),
        max_value=Decimal("100.00"),
        required=False,
        default=Decimal("0.00"),
    )


class TaskCreateSerializer(serializers.Serializer):
    task_type = serializers.ChoiceField(
        choices=[
            choice
            for choice in Task.Type.choices
            if choice[0]
            not in {
                Task.Type.NODE_LIFECYCLE,
                Task.Type.REPOSITORY_OPERATION,
                Task.Type.STORAGE_PROVIDER_VALIDATION,
            }
        ]
    )
    display_name = serializers.CharField(max_length=255, trim_whitespace=True)
    trigger_type = serializers.ChoiceField(
        choices=Task.TriggerType.choices,
        default=Task.TriggerType.MANUAL,
    )
    request_payload = serializers.JSONField(required=False)
    resources = TaskResourceInputSerializer(many=True, required=False)
    steps = TaskStepInputSerializer(many=True, required=False)

    def validate_resources(self, resources):
        if sum(1 for resource in resources if resource.get("is_primary")) > 1:
            raise serializers.ValidationError("A task can have at most one primary resource.")
        return resources

    def create(self, validated_data):
        organization_id = self.context["organization_id"]
        return create_task(
            organization_id=organization_id,
            task_type=validated_data["task_type"],
            display_name=validated_data["display_name"],
            trigger_type=validated_data["trigger_type"],
            request_payload=validated_data.get("request_payload") or None,
            resources=validated_data.get("resources") or [],
            steps=validated_data.get("steps") or None,
        )


class TaskCancelSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)


class TaskRetrySerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, max_length=500)
