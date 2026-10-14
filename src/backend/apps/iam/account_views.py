"""Organization membership directory and creation are account-level surfaces."""

from django.utils.translation import gettext as _

from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.models import Membership
from apps.iam.services.account_setup import assert_account_ready
from apps.iam.services.organization_creation import (
    create_user_organization,
    organization_creation_options,
)
from common.extension_spi import get_authz_provider


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
                or provider.get_org_role(request.user, row.organization.key)
            ]
        )


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
