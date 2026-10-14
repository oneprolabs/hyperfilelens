"""
IAM serializers.
"""

from django.utils.translation import gettext as _

from django.contrib.auth import get_user_model
from collections.abc import Mapping

from rest_framework import serializers

from apps.iam.models import Membership, Organization, PersonalApiKey
from apps.iam.services.membership_service import (
    MembershipPolicyError,
    assert_role_assignable,
)


User = get_user_model()


class OrganizationSerializer(serializers.ModelSerializer):
    owner_email = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = Organization
        fields = ["id", "key", "name", "is_active", "created_at", "updated_at", "owner_email", "member_count"]
        read_only_fields = ["id", "created_at", "updated_at"]
        extra_kwargs = {
            "key": {"required": False},
        }

    def get_owner_email(self, obj):
        from common.extension_spi import get_authz_provider

        provider = get_authz_provider()
        if provider is not None:
            email = provider.resolve_owner_email(obj)
            if email:
                return email
        # Community: sole / first active affiliation.
        owner = (
            obj.memberships.filter(is_active=True)
            .select_related("user")
            .order_by("id")
            .first()
        )
        return owner.user.email if owner else None

    def get_member_count(self, obj):
        count = getattr(obj, "member_count", None)
        if count is not None:
            return count
        return obj.memberships.filter(is_active=True).count()


class MembershipSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)
    organization_name = serializers.CharField(source="organization.name", read_only=True)
    role = serializers.SerializerMethodField()
    email = serializers.EmailField(write_only=True, required=False, max_length=254)
    management = serializers.SerializerMethodField()
    user_is_active = serializers.BooleanField(source="user.is_active", read_only=True)
    requires_password_change = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = [
            "id",
            "organization",
            "organization_name",
            "user",
            "user_email",
            "user_username",
            "role",
            "is_active",
            "created_at",
            "email",
            "management",
            "user_is_active",
            "requires_password_change",
        ]
        read_only_fields = ["id", "created_at", "organization"]
        extra_kwargs = {"user": {"required": False}}

    def get_role(self, obj):
        from apps.iam.services.membership_service import authoritative_role

        roles = self.context.setdefault("_membership_display_roles", {})
        if obj.pk not in roles:
            roles[obj.pk] = authoritative_role(obj)
        return roles[obj.pk]

    def get_requires_password_change(self, obj):
        from apps.iam.services.account_setup import requires_password_change

        return requires_password_change(obj.user)

    def get_management(self, obj):
        from apps.iam.services.member_management import (
            membership_management_permissions, membership_management_profile,
        )

        request = self.context.get("request")
        if request is None:
            return {}
        if "_member_management_profile" not in self.context:
            self.context["_member_management_profile"] = membership_management_profile(request)
        return membership_management_permissions(
            request, obj, profile=self.context["_member_management_profile"], role=self.get_role(obj),
        )

    def validate_role(self, value):
        if value in Membership.Role.values:
            assert_role_assignable(value)
            return value
        from apps.iam.permissions_org import get_membership
        from common.extension_spi import get_authz_provider

        request = self.context.get("request")
        membership = get_membership(request) if request is not None else None
        provider = get_authz_provider()
        resolver = getattr(provider, "is_custom_org_role", None) if provider is not None else None
        if (
            membership is None
            or not callable(resolver)
            or not resolver(organization_id=membership.organization_id, role=value)
        ):
            assert_role_assignable(value)
        return value

    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            return super().to_internal_value(data)
        allowed = {"role", "is_active"} if self.instance is not None else {"user", "email", "role", "is_active"}
        unknown = set(data) - allowed
        if unknown:
            raise serializers.ValidationError({key: "Unsupported membership field." for key in sorted(unknown)})
        # role is SerializerMethodField on output; accept writable role on input.
        ret = super().to_internal_value(data)
        if "role" in data:
            role = data.get("role")
            self.validate_role(role)
            ret["role"] = role
        return ret

    def validate(self, attrs):
        instance = getattr(self, "instance", None)
        from apps.iam.services.membership_service import (
            active_owner_count,
            authoritative_role,
        )

        if instance is None:
            email = attrs.pop("email", None)
            if email and "user" in attrs:
                raise serializers.ValidationError({"email": "Use either email or user, not both."})
            if email:
                # Exact match only: no customer-facing global account directory.
                matches = list(User.objects.filter(email__iexact=email.strip())[:2])
                if len(matches) != 1:
                    raise serializers.ValidationError({"email": "An eligible active account could not be found."})
                attrs["user"] = matches[0]
            if "user" not in attrs:
                raise serializers.ValidationError({"email": "An existing account email is required."})
            from apps.iam.services.member_management import assert_eligible_member_account

            assert_eligible_member_account(attrs["user"])
            return attrs
        if not attrs:
            raise serializers.ValidationError("Provide a role or membership status.")
        if authoritative_role(instance) == Membership.Role.OWNER:
            if "role" in attrs and attrs["role"] != Membership.Role.OWNER:
                raise serializers.ValidationError(
                    {"role": "Owner role cannot be changed via membership API."}
                )
        if attrs.get("is_active") is False and instance.is_active:
            if active_owner_count(instance.organization, exclude_pk=instance.pk) < 1:
                raise serializers.ValidationError(
                    {"is_active": "Cannot deactivate the organization owner."}
                )
        return attrs

    def create(self, validated_data):
        from apps.iam.services.membership_service import create_org_membership

        organization = validated_data["organization"]
        try:
            return create_org_membership(
                organization=organization,
                user=validated_data["user"],
                role=validated_data.get("role", Membership.Role.OPERATOR),
                is_active=validated_data.get("is_active", True),
            )
        except MembershipPolicyError as exc:
            raise serializers.ValidationError(exc.detail) from exc

    def update(self, instance, validated_data):
        from apps.iam.services.membership_service import update_org_membership

        try:
            return update_org_membership(instance, **validated_data)
        except MembershipPolicyError as exc:
            raise serializers.ValidationError(exc.detail) from exc


class MemberOnboardingSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, max_length=254)
    user = serializers.IntegerField(required=False, min_value=1)
    role = serializers.CharField(required=False, max_length=64)
    is_active = serializers.BooleanField(default=True)

    def to_internal_value(self, data):
        if isinstance(data, Mapping):
            unknown = set(data) - {"email", "user", "role", "is_active"}
            if unknown:
                raise serializers.ValidationError({key: "Unsupported membership field." for key in sorted(unknown)})
        return super().to_internal_value(data)

    def validate(self, attrs):
        if bool(attrs.get("email")) == bool(attrs.get("user")):
            raise serializers.ValidationError({"email": _("Provide an email or an existing user ID, not both.")})
        if attrs.get("role") is not None:
            MembershipSerializer(context=self.context).validate_role(attrs["role"])
        return attrs


class PersonalApiKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = PersonalApiKey
        fields = ["id", "name", "token", "is_active", "created_at"]
        read_only_fields = ["id", "token", "created_at"]
