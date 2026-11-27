"""REST views for ``NodeToken`` lifecycle."""

from rest_framework import status, viewsets
from rest_framework.response import Response

from apps.iam.org_context import require_org_matching_body
from apps.iam.organization_policy import require_organization_capability
from apps.node.api import permissions as node_permissions
from apps.node.api.serializers import NodeTokenCreateSerializer, NodeTokenSerializer
from apps.node.api.views.mixins import SoftDeleteDestroyMixin
from apps.node.models import NodeToken
from common.drf.org_scoped import OrgScopedMixin


class NodeTokenViewSet(
    OrgScopedMixin,
    SoftDeleteDestroyMixin,
    viewsets.ModelViewSet,
):
    permission_classes = [
        node_permissions.IsAuthenticated,
        node_permissions.IsOrgWriter,
    ]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [
                node_permissions.IsAuthenticated(),
                node_permissions.IsOrgStaffReader(),
            ]
        return super().get_permissions()

    def get_serializer_class(self):
        if self.action == "create":
            return NodeTokenCreateSerializer
        return NodeTokenSerializer

    def get_org_scoped_queryset(self):
        return (
            NodeToken.objects.select_related("organization")
            .all()
            .order_by(
                "-created_at",
                "-id",
            )
        )

    def create(self, request, *args, **kwargs):
        ser = NodeTokenCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        org = require_org_matching_body(request, ser.validated_data.pop("org", None))
        node_permissions.require_gateway_enrollment_creation(
            request, org, ser.validated_data["role"],
        )
        if ser.validated_data.get("role") == "proxy":
            require_organization_capability(org.pk, "proxy_hosts", request=request)
        token_row = ser.save(organization=org, created_by=request.user)
        out = NodeTokenSerializer(
            token_row,
            context={
                **self.get_serializer_context(),
                "include_token": True,
            },
        )
        return Response(out.data, status=status.HTTP_201_CREATED)
