from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator
from apps.iam.resource_access import assert_resource_access
from apps.protection.api.serializers.backup_target_validation import (
    BackupTargetValidationSerializer,
)
from apps.protection.services.backup_target_validation import validate_backup_targets


class BackupTargetValidationView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        org = require_org(request)
        serializer = BackupTargetValidationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for source in serializer.validated_data["sources"]:
            assert_resource_access(request, "repository", source["repository_id"], action="resource.view")
            assert_resource_access(
                request,
                "node" if source["source_type"] == "agent" else "source_resource",
                source["source_ref_id"],
                action="resource.view",
            )
        return Response(
            validate_backup_targets(
                organization_id=org.id,
                sources=serializer.validated_data["sources"],
            )
        )
