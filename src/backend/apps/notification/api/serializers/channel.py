from rest_framework import serializers

from apps.notification.models import NotificationChannel


class NotificationChannelSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source="channel_type")
    enabled = serializers.BooleanField(source="is_active")
    policies_count = serializers.SerializerMethodField(read_only=True)
    last_delivery_status = serializers.SerializerMethodField(read_only=True)
    last_delivery_at = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = NotificationChannel
        fields = [
            "id",
            "organization",
            "name",
            "type",
            "enabled",
            "config",
            "policies_count",
            "last_delivery_status",
            "last_delivery_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "organization", "created_at", "updated_at"]

    def get_policies_count(self, obj):
        by_channel = self.context.get("policies_count_by_channel") or {}
        return int(by_channel.get(int(obj.id), 0) or 0)

    def get_last_delivery_status(self, obj):
        by_channel = self.context.get("last_delivery_by_channel") or {}
        item = by_channel.get(int(obj.id)) or {}
        return item.get("status")

    def get_last_delivery_at(self, obj):
        by_channel = self.context.get("last_delivery_by_channel") or {}
        item = by_channel.get(int(obj.id)) or {}
        return item.get("sent_at")

    def validate_config(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Config must be an object.")
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        channel_type = attrs.get("channel_type") or getattr(self.instance, "channel_type", "")
        config = attrs.get("config")
        if channel_type == "email" and config is not None:
            recipients = config.get("to_emails") or config.get("to") or config.get("recipients") or []
            if isinstance(recipients, str):
                recipients = [item.strip() for item in recipients.split(",") if item.strip()]
            elif isinstance(recipients, list):
                recipients = [str(item).strip() for item in recipients if str(item).strip()]
            if not recipients:
                raise serializers.ValidationError({"config": "Email channel requires to_emails."})
        return attrs

    def update(self, instance, validated_data):
        config = validated_data.get("config")
        if config and isinstance(config, dict):
            existing = instance.config or {}
            sensitive = ["smtp_password", "token", "secret", "authorization", "api_key"]
            config = dict(config)
            clear_secret = config.pop("clear_secret", False) is True
            for field in sensitive:
                # Secret values are deliberately omitted from edit forms and
                # masked in API responses. Do not let an edit that changes an
                # unrelated field erase the stored credential.
                if (
                    field not in config
                    or config.get(field) in ("", None, "********")
                ) and existing.get(field) and not (field == "secret" and clear_secret):
                    config[field] = existing[field]
            validated_data["config"] = config
        return super().update(instance, validated_data)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        config = dict(data.get("config") or {})
        for key in ["smtp_password", "token", "secret", "authorization", "api_key"]:
            if config.get(key):
                config[key] = "********"
        headers = config.get("headers")
        if isinstance(headers, dict):
            for key in list(headers.keys()):
                if key.lower() in {"authorization", "x-api-key"}:
                    headers[key] = "********"
        data["config"] = config
        return data

    def create(self, validated_data):
        org = self.context.get("organization")
        if org is not None:
            validated_data["organization"] = org
        return super().create(validated_data)


class BulkChannelStateSerializer(serializers.Serializer):
    """Request payload for bulk enable/disable of notification channels."""

    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=500,
    )
    is_active = serializers.BooleanField()


class BulkChannelDeleteSerializer(serializers.Serializer):
    """Request payload for bulk delete of notification channels."""

    ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        max_length=500,
    )
