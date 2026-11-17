"""Organization membership directory and creation are account-level surfaces."""

from typing import Any

from django.utils.translation import gettext as _
from django.db import transaction

from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.models import Membership, Organization
from apps.iam.services.account_setup import assert_account_ready
from apps.iam.services.organization_creation import (
    create_user_organization,
    organization_creation_options,
)
from common.extension_spi import get_authz_provider


def can_enter_organization(user: Any, organization: Organization, provider: Any) -> bool:
    """Use live assignment state; global authority alone is not an affiliation."""
    if not provider.get_org_role(user, organization.key):
        return False
    factory = getattr(provider, "authorization_context", None)
    if callable(factory):
        context = factory(user, organization.key)
        return bool(context is not None and context.valid and context.fence_queryset(
            Membership.objects.filter(
                user=user, organization=organization, is_active=True,
                user__is_active=True, organization__is_active=True,
            ),
        ).exists())
    return True


class MyOrganizationsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        assert_account_ready(request.user)
        provider = get_authz_provider()
        rows = (
            Membership.objects.filter(
                user=request.user,
                is_active=True,
                organization__is_active=True,
            )
            .select_related("organization")
            .order_by("organization__name", "organization_id")
        )
        return Response(
            [
                {
                    "id": row.organization_id,
                    "key": row.organization.key,
                    "name": row.organization.name,
                }
                for row in rows
                if provider is None
                or can_enter_organization(request.user, row.organization, provider)
            ]
        )


class SwitchOrganizationSerializer(serializers.Serializer):
    org_key = serializers.CharField(max_length=64)


class SwitchOrganizationView(APIView):
    """Validate a tab-local switch without mutating shared login cookies/session."""

    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        assert_account_ready(request.user)
        provider = get_authz_provider()
        if provider is None:
            raise PermissionDenied("Organization switching requires Enterprise.")
        serializer = SwitchOrganizationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["org_key"]
        org = Organization.objects.select_for_update(no_key=True).filter(
            key=key, is_active=True,
        ).first()
        if (
            org is None
            or not Membership.objects.filter(
                user=request.user, organization=org, is_active=True, user__is_active=True,
            ).exists()
            or not can_enter_organization(request.user, org, provider)
        ):
            raise PermissionDenied("This organization is unavailable for this account.")
        from apps.iam.access import get_access_profile

        profile = get_access_profile(request.user, org_key=org.key)
        if profile["org_key"] != org.key:
            raise PermissionDenied("This organization is unavailable for this account.")
        return Response({"access_profile": profile})


class OrganizationCreationOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        assert_account_ready(request.user)
        return Response(organization_creation_options(request.user))


class OrganizationCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, trim_whitespace=True)

    def to_internal_value(self, data):
        if isinstance(data, dict) and set(data) != {"name"}:
            raise serializers.ValidationError(
                _("Only an organization name may be provided.")
            )
        return super().to_internal_value(data)


class CreateOrganizationView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = OrganizationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        org = create_user_organization(request, name=serializer.validated_data["name"])
        return Response({"id": org.pk, "key": org.key, "name": org.name}, status=201)
