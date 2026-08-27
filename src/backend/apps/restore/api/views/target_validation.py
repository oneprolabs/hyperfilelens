from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgOperator
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
        return Response(
            validate_restore_targets(
                organization_id=org.id,
                targets=serializer.validated_data["targets"],
            )
        )
