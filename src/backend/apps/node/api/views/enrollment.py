"""
Enrollment API entrypoints (tokens, bootstrap stubs, agent releases).

Implementation is split across ``enrollment_helpers``, ``bootstrap``, and
``artifact_release``.
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org_matching_body
from apps.iam.organization_policy import require_organization_capability
from apps.node.api import permissions as node_permissions
from apps.node.api.serializers import NodeTokenCreateSerializer, NodeTokenSerializer
from apps.node.api.views.artifact_release import AgentReleaseView, AgentReleasesAuthView
from apps.node.api.views.enrollment_helpers import enrollment_health

__all__ = [
    "AgentReleaseView",
    "AgentReleasesAuthView",
    "EnrollmentTokenCreateView",
    "enrollment_health",
]


class EnrollmentTokenCreateView(APIView):
    """Legacy install path; prefer ``POST /node-tokens/``."""

    permission_classes = [
        node_permissions.IsAuthenticated,
        node_permissions.IsOrgWriter,
    ]

    def post(self, request):
        ser = NodeTokenCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        org = require_org_matching_body(request, ser.validated_data.pop("org", None))
        node_permissions.require_gateway_enrollment_creation(
            request, org, ser.validated_data["role"],
        )
        if ser.validated_data.get("role") == "proxy":
            require_organization_capability(org.pk, "proxy_hosts", request=request)
        token_row = ser.save(organization=org, created_by=request.user)
        data = NodeTokenSerializer(
            token_row,
            context={"request": request, "include_token": True},
        ).data
        return Response(
            {
                "org": org.key,
                "role": data["role"],
                "token": data["token"],
                "created_at": data["created_at"],
                "tls_verify": data["tls_verify"],
            },
            status=status.HTTP_201_CREATED,
        )
