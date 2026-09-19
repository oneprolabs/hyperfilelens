"""
IAM views.
"""

from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.http import JsonResponse

from rest_framework import permissions, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.iam.models import Membership, Organization, PersonalApiKey
from apps.iam.permissions_org import (
    IsOrgAdmin,
    IsOrgMemberReader,
    get_membership,
    resolve_org_key,
)
from apps.iam.serializers import (
    MembershipSerializer,
    OrganizationSerializer,
    PersonalApiKeySerializer,
)
from apps.iam.services.membership_service import assert_can_delete, MembershipPolicyError

User = get_user_model()


def health(_request):
    return JsonResponse({"app": "iam", "status": "ok"})


class OrganizationViewSet(viewsets.ModelViewSet):
    serializer_class = OrganizationSerializer
    permission_classes = [IsAuthenticated]
    # Organization is currently a read-only governance surface. Creation and
    # mutation must go through the registration/organization services so quota,
    # owner membership, and EE initialization cannot be bypassed.
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        qs = (
            Organization.objects.annotate(
                member_count=Count("memberships", filter=Q(memberships__is_active=True)),
            )
            .filter(
                memberships__user=self.request.user,
                memberships__is_active=True,
            )
            .distinct()
        )
        org_key = resolve_org_key(self.request)
        if org_key:
            qs = qs.filter(key=org_key)
        return qs.order_by("id")


class MembershipViewSet(viewsets.ModelViewSet):
    serializer_class = MembershipSerializer

    def get_permissions(self):
        permission_class = (
            IsOrgMemberReader
            if self.request.method in permissions.SAFE_METHODS
            else IsOrgAdmin
        )
        return [IsAuthenticated(), permission_class()]

    def get_queryset(self):
        membership = get_membership(self.request)
        if membership is None:
            return Membership.objects.none()
        return (
            Membership.objects.select_related("user", "organization")
            .filter(organization_id=membership.organization_id)
            .order_by("id")
        )

    def perform_create(self, serializer):
        membership = get_membership(self.request)
        if membership is None:
            return
        serializer.save(organization=membership.organization)

    def perform_destroy(self, instance):
        from django.db import transaction

        try:
            assert_can_delete(instance)
        except MembershipPolicyError as exc:
            from rest_framework.exceptions import ValidationError

            raise ValidationError(exc.detail) from exc
        user_id = instance.user_id
        organization_id = instance.organization_id
        from apps.iam.services.membership_service import sync_member_role

        with transaction.atomic():
            instance.delete()
            sync_member_role(user_id=user_id, organization_id=organization_id, role=None)


class PersonalApiKeyViewSet(viewsets.ModelViewSet):
    serializer_class = PersonalApiKeySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return PersonalApiKey.objects.filter(user=self.request.user).order_by(
            "-created_at"
        )

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
