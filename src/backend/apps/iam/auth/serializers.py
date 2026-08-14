"""
Serializers for authentication and user profile.
"""

from django.conf import settings
from django.contrib.auth.models import User

from rest_framework import serializers

from apps.iam.profile_models import Profile
from apps.iam.access import get_access_profile
from apps.iam.services.login_audit import get_security_audit, serialize_security_audit


def get_registered_at(user):
    try:
        profile = user.profile
        if profile.registered_at:
            return profile.registered_at
    except Profile.DoesNotExist:
        pass
    return user.date_joined


class UserDetailsSerializer(serializers.ModelSerializer):
    """
    Minimal user details serializer.
    """

    language = serializers.CharField(
        max_length=32,
        required=False,
        source="profile.language",
    )
    timezone = serializers.SerializerMethodField()
    access_profile = serializers.SerializerMethodField()
    registered_at = serializers.SerializerMethodField()
    security_audit = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_staff",
            "language",
            "timezone",
            "access_profile",
            "registered_at",
            "security_audit",
        ]
        read_only_fields = ["is_staff"]

    @staticmethod
    def validate_language(value):
        if value not in dict(settings.LANGUAGES):
            raise serializers.ValidationError(
                f"Language {value!r} is not installed.",
            )
        return value

    def update(self, instance, validated_data):
        profile_data = validated_data.pop("profile", {})
        instance = super().update(instance, validated_data)
        if "language" in profile_data:
            try:
                profile = instance.profile
            except Profile.DoesNotExist:
                profile = Profile.objects.create(user=instance)
            profile.language = profile_data["language"]
            profile.full_clean()
            profile.save(update_fields=["language"])
        return instance

    def to_representation(self, instance):
        representation = super().to_representation(instance)
        if not representation.get("language"):
            representation["language"] = "en"
        return representation

    @staticmethod
    def get_timezone(obj):
        try:
            return obj.profile.timezone
        except Profile.DoesNotExist:
            return "UTC"

    def get_access_profile(self, obj):
        request = self.context.get("request")
        org_key = None
        if request is not None:
            org_key = request.headers.get("X-Org-Key") or request.query_params.get("org")
        return get_access_profile(obj, org_key=org_key)

    @staticmethod
    def get_registered_at(obj):
        value = get_registered_at(obj)
        return value.isoformat() if value else None

    @staticmethod
    def get_security_audit(obj):
        return serialize_security_audit(get_security_audit(obj))
