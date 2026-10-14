from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator
from apps.iam.resource_access import assert_resource_access
from apps.protection.models import BackupSourceSnapshot
from rest_framework.exceptions import NotFound
from apps.restore.api.serializers.target_validation import (
    RestoreTargetValidationSerializer,
)
from apps.restore.services.target_validation import validate_restore_targets


class RestoreTargetValidationView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        org = require_org(request)
        serializer = RestoreTargetValidationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for target in serializer.validated_data["targets"]:
            assert_resource_access(
                request,
                "node" if target["target_type"] == "agent" else "source_resource",
                target["target_ref_id"],
                action="protection.restore_plans.run",
            )
            snapshot = BackupSourceSnapshot.objects.filter(
                organization_id=org.id,
                pk=target["source_snapshot_id"],
            ).first()
            if snapshot is None:
                raise NotFound("Snapshot not found.")
            assert_resource_access(
                request,
                "backup_config",
                snapshot.backup_config_id,
                action="protection.restore_plans.run",
            )
        return Response(
            validate_restore_targets(
                organization_id=org.id,
                targets=serializer.validated_data["targets"],
            )
        )
