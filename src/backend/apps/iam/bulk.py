"""Validated, bounded requests for atomic organization-member actions."""

from collections.abc import Mapping

from rest_framework import serializers


class MembershipBulkActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=("activate", "deactivate", "remove"))
    membership_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1, max_value=2**63 - 1),
        min_length=1,
        max_length=200,
    )

    def to_internal_value(self, data):
        if isinstance(data, Mapping) and set(data) - set(self.fields):
            raise serializers.ValidationError({
                key: "Unsupported bulk member field."
                for key in sorted(set(data) - set(self.fields))
            })
        return super().to_internal_value(data)

    def validate_membership_ids(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Select each membership only once.")
        return value
