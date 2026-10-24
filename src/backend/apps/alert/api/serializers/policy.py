from rest_framework import serializers

from apps.alert.constants import (
    METRICS_BY_RESOURCE_TYPE,
    AlertType,
    PolicyScope,
    ResourceType,
)
from apps.alert.models import AlertPolicy
from apps.alert.selectors.interface import notification_channels_for_policy
from apps.alert.services.internal.metadata_resources import selected_resource_options
from apps.alert.services.internal.recovery import supports_numeric_recovery

_DEFAULT_EVALUATION_INTERVAL_SECONDS = 60


class AlertPolicySerializer(serializers.ModelSerializer):
    notification_channels = serializers.SerializerMethodField(read_only=True)
    monitoring_resources = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = AlertPolicy
        fields = [
            "id",
            "organization",
            "name",
            "description",
            "type",
            "severity",
            "enabled",
            "resource_type",
            "scope",
            "resource_ids",
            "trigger_rule",
            "recovery_rule",
            "notification_channel_ids",
            "notification_channels",
            "monitoring_resources",
            "last_evaluated_at",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "organization",
            "created_by",
            "created_at",
            "updated_at",
            "notification_channels",
            "monitoring_resources",
            "last_evaluated_at",
        ]

    def validate(self, attrs):
        instance = self.instance
        data = {}
        if instance:
            for field in self.Meta.fields:
                if field in ("notification_channels",):
                    continue
                if hasattr(instance, field):
                    data[field] = getattr(instance, field)
        data.update(attrs)

        alert_type = data.get("type")
        scope = data.get("scope", PolicyScope.SELECTED)
        resource_ids = data.get("resource_ids") or []
        resource_type = data.get("resource_type")
        trigger_rule = data.get("trigger_rule") or {}

        try:
            duration_seconds = int(trigger_rule.get("duration_seconds") or 0)
        except (TypeError, ValueError):
            duration_seconds = -1
        if duration_seconds < 0:
            raise serializers.ValidationError(
                {"trigger_rule": "Duration must be zero or greater."}
            )

        recovery_rule = data.get("recovery_rule") or {}
        if instance is None and alert_type == AlertType.TASK and "enabled" not in recovery_rule:
            # New rules use manual resolution. Preserve explicitly supplied
            # legacy settings and never rewrite existing policies on read/edit.
            recovery_rule = {**recovery_rule, "enabled": False}
            attrs["recovery_rule"] = recovery_rule
        previous_recovery = (instance.recovery_rule or {}) if instance else {}
        previous_trigger = (instance.trigger_rule or {}) if instance else {}
        recovery_changed = (
            instance is None
            or alert_type != instance.type
            or supports_numeric_recovery(alert_type, trigger_rule)
            != supports_numeric_recovery(instance.type, previous_trigger)
            or any(
                recovery_rule.get(key) != previous_recovery.get(key)
                for key in ("enabled", "operator", "threshold", "duration_seconds")
            )
            or (
                "threshold" not in recovery_rule
                and trigger_rule.get("threshold") != previous_trigger.get("threshold")
            )
        )
        if (
            recovery_changed
            and supports_numeric_recovery(alert_type, trigger_rule)
            and recovery_rule
            and recovery_rule.get("enabled") is not False
        ):
            recovery_operator = recovery_rule.get("operator")
            if recovery_operator and recovery_operator not in {
                ">",
                ">=",
                "<",
                "<=",
                "==",
                "!=",
            }:
                raise serializers.ValidationError(
                    {"recovery_rule": "Recovery operator is not supported."}
                )
            try:
                recovery_duration = int(recovery_rule.get("duration_seconds") or 0)
                float(recovery_rule.get("threshold", trigger_rule.get("threshold", 0)))
            except (TypeError, ValueError):
                raise serializers.ValidationError(
                    {"recovery_rule": "Recovery threshold and duration must be numeric."}
                )
            if recovery_duration < 0:
                raise serializers.ValidationError(
                    {"recovery_rule": "Recovery duration must be zero or greater."}
                )

        if alert_type == AlertType.METRIC:
            # Legacy policies predate this field. Normalize the default on write
            # so old and new evaluators share the same cadence.
            if trigger_rule.get("evaluation_interval_seconds") in (None, ""):
                trigger_rule = {
                    **trigger_rule,
                    "evaluation_interval_seconds": _DEFAULT_EVALUATION_INTERVAL_SECONDS,
                }
                data["trigger_rule"] = trigger_rule
                attrs["trigger_rule"] = trigger_rule
            try:
                evaluation_interval = int(
                    trigger_rule.get("evaluation_interval_seconds")
                    or _DEFAULT_EVALUATION_INTERVAL_SECONDS
                )
            except (TypeError, ValueError):
                evaluation_interval = 0
            if evaluation_interval < _DEFAULT_EVALUATION_INTERVAL_SECONDS:
                raise serializers.ValidationError(
                    {"trigger_rule": "Evaluation interval must be at least 60 seconds."}
                )

        if not resource_type:
            raise serializers.ValidationError({"resource_type": "This field is required."})

        if alert_type == AlertType.METRIC and not METRICS_BY_RESOURCE_TYPE.get(resource_type):
            raise serializers.ValidationError({
                "resource_type": (
                    "This resource type does not support metric alerts. "
                    "Choose a resource type with collected metrics, such as Control Plane."
                )
            })

        if alert_type in (AlertType.METRIC, AlertType.SYSTEM):
            allowed_metrics = METRICS_BY_RESOURCE_TYPE.get(resource_type, [])
            metric_key = trigger_rule.get("metric_key")
            if metric_key and metric_key not in allowed_metrics:
                raise serializers.ValidationError(
                    {
                        "trigger_rule": (
                            f"Metric '{metric_key}' is not available for "
                            f"resource type '{resource_type}'."
                        )
                    }
                )

        if scope == PolicyScope.SELECTED and resource_ids and resource_type in {
            ResourceType.AGENT_PROXY,
            ResourceType.SYNC_PROXY,
            ResourceType.GATEWAY,
        }:
            from apps.node.models import Node
            from apps.node.models.base import NodeRole

            role_by_resource_type = {
                ResourceType.AGENT_PROXY: NodeRole.AGENT,
                ResourceType.SYNC_PROXY: NodeRole.PROXY,
                ResourceType.GATEWAY: NodeRole.GATEWAY,
            }
            node_ids = []
            for raw_id in resource_ids:
                try:
                    node_ids.append(int(raw_id))
                except (TypeError, ValueError):
                    raise serializers.ValidationError(
                        {"resource_ids": "Selected resources must be valid node IDs."}
                    )
            from common.resource_scope import ResourceScope

            org = self.context.get("organization") or getattr(
                instance, "organization", None
            )
            owner_scope = None
            if instance is not None:
                owner_scope = instance.owner_scope
            elif self.context.get("owner_scope"):
                owner_scope = self.context.get("owner_scope")
            elif org is not None:
                owner_scope = ResourceScope.ORGANIZATION

            node_qs = Node.objects.filter(
                id__in=node_ids,
                role=role_by_resource_type[resource_type],
            )
            # Platform-owned policies target platform-scoped nodes (org is None).
            if owner_scope == ResourceScope.PLATFORM or org is None:
                node_qs = node_qs.filter(
                    scope=ResourceScope.PLATFORM,
                    organization__isnull=True,
                )
            else:
                node_qs = node_qs.filter(organization=org)
            found = set(node_qs.values_list("id", flat=True))
            if set(node_ids) != found:
                raise serializers.ValidationError(
                    {
                        "resource_ids": (
                            "Selected resources do not match the resource type "
                            "or organization."
                        )
                    }
                )

        if (
            scope == PolicyScope.SELECTED
            and alert_type != AlertType.EVENT
            and resource_type != "system"
            and not resource_ids
        ):
            raise serializers.ValidationError(
                {"resource_ids": "Required when scope is selected."}
            )

        required_by_type = {
            AlertType.METRIC: [
                "metric_key",
                "operator",
                "threshold",
                "duration_seconds",
            ],
            AlertType.AVAILABILITY: ["check_type", "timeout_seconds", "duration_seconds"],
            AlertType.TASK: ["task_type", "event_type"],
            AlertType.EVENT: ["event_category", "event_types"],
            AlertType.SYSTEM: ["check_type", "duration_seconds"],
        }
        missing = [
            key
            for key in required_by_type.get(alert_type, [])
            if trigger_rule.get(key) in (None, "", [])
        ]
        if missing:
            raise serializers.ValidationError(
                {"trigger_rule": f"Missing required fields: {', '.join(missing)}"}
            )

        org = self.context.get("organization")
        if org is None and instance is not None:
            org = instance.organization
        owner_scope = None
        if instance is not None:
            owner_scope = instance.owner_scope
        elif self.context.get("owner_scope"):
            owner_scope = self.context.get("owner_scope")
        elif org is not None:
            from common.resource_scope import ResourceScope

            owner_scope = ResourceScope.ORGANIZATION
        channel_ids = data.get("notification_channel_ids") or []
        if channel_ids and (org is not None or owner_scope):
            from common.resource_scope import ResourceScope
            from apps.notification.models import NotificationChannel

            int_ids = []
            for raw in channel_ids:
                try:
                    int_ids.append(int(raw))
                except (TypeError, ValueError):
                    raise serializers.ValidationError(
                        {"notification_channel_ids": f"Invalid channel id: {raw}"}
                    )
            channel_qs = NotificationChannel.objects.filter(id__in=int_ids, is_active=True)
            if owner_scope == ResourceScope.PLATFORM:
                channel_qs = channel_qs.filter(
                    owner_scope=ResourceScope.PLATFORM,
                    organization__isnull=True,
                )
            elif org is not None:
                channel_qs = channel_qs.filter(
                    owner_scope=ResourceScope.ORGANIZATION,
                    organization=org,
                )
            found = set(channel_qs.values_list("id", flat=True))
            missing_ids = [cid for cid in int_ids if cid not in found]
            if missing_ids:
                raise serializers.ValidationError(
                    {
                        "notification_channel_ids": (
                            f"Unknown or inactive channels: {missing_ids}"
                        )
                    }
                )
        return attrs

    def create(self, validated_data):
        from common.resource_scope import ResourceScope

        request = self.context.get("request")
        org = self.context.get("organization")
        owner_scope = self.context.get("owner_scope")
        if owner_scope == ResourceScope.PLATFORM:
            validated_data["organization"] = None
            validated_data["owner_scope"] = ResourceScope.PLATFORM
        elif org is not None:
            validated_data["organization"] = org
            validated_data["owner_scope"] = ResourceScope.ORGANIZATION
        if request and request.user.is_authenticated:
            validated_data["created_by"] = request.user.id
        return super().create(validated_data)

    def get_notification_channels(self, obj):
        org = obj.organization
        return notification_channels_for_policy(obj, org)

    def get_monitoring_resources(self, obj):
        if obj.scope != PolicyScope.SELECTED and obj.resource_type != ResourceType.SYSTEM:
            return []
        return selected_resource_options(
            organization_id=obj.organization_id,
            resource_type=obj.resource_type,
            resource_ids=obj.resource_ids or [],
            owner_scope=obj.owner_scope,
        )


class BulkPolicyStateSerializer(serializers.Serializer):
    """Request payload for bulk enable/disable of alert policies."""

    ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        max_length=500,
    )
    enabled = serializers.BooleanField()


class BulkPolicyDeleteSerializer(serializers.Serializer):
    """Request payload for bulk delete of alert policies."""

    ids = serializers.ListField(
        child=serializers.UUIDField(),
        allow_empty=False,
        max_length=500,
    )
