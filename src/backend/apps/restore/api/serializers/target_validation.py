from rest_framework import serializers


class RestoreTargetValidationTargetSerializer(serializers.Serializer):
    key = serializers.CharField(max_length=128)
    source_snapshot_id = serializers.IntegerField(min_value=1)
    target_type = serializers.ChoiceField(choices=[("agent", "agent"), ("nas", "nas")])
    target_ref_id = serializers.IntegerField(min_value=1)


class RestoreTargetValidationSerializer(serializers.Serializer):
    targets = RestoreTargetValidationTargetSerializer(
        many=True,
        allow_empty=False,
        max_length=500,
    )

    def validate_targets(self, value):
        keys = [str(item["key"]) for item in value]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("Restore target row keys must be unique.")
        return value
