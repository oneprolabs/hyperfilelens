"""Installed-node performance synchronization, separate from business tasks."""

from django.db import transaction
from django.core.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer

from apps.node.api.permissions import AllowAny
from apps.node.models import Node
from apps.node.services.internal.agent_ws_auth import validate_agent_ws_credentials
from apps.node.services.internal.performance_policy import (
    POLICY_KEY,
    STATUS_KEY,
    normalize_report,
)


class NodePerformancePolicyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    parser_classes = [JSONParser]
    renderer_classes = [JSONRenderer]

    def _node(self, request):
        identifier = str(request.query_params.get("node_id", ""))
        token = str(request.headers.get("X-Node-Token", ""))
        org_key = str(request.headers.get("X-Org-Key", ""))
        if (
            not identifier.isascii()
            or not identifier.isdigit()
            or len(identifier) > 19
            or not 0 < int(identifier) <= 2**63 - 1
            or not token
            or len(token) > 512
            or not org_key
            or len(org_key) > 128
        ):
            return None
        node = Node.objects.filter(
            pk=identifier,
            organization__key=org_key,
            organization__is_active=True,
            is_deleted=False,
        ).first()
        if node is None or not validate_agent_ws_credentials(node.id, token):
            return None
        return node

    def get(self, request):
        node = self._node(request)
        if node is None:
            return Response({"detail": "Invalid node credentials."}, status=401)
        metadata = node.metadata if isinstance(node.metadata, dict) else {}
        response = Response({"policy": metadata.get(POLICY_KEY)})
        response["Cache-Control"] = "no-store"
        return response

    def post(self, request):
        node = self._node(request)
        if node is None:
            return Response({"detail": "Invalid node credentials."}, status=401)
        with transaction.atomic():
            node = Node.objects.select_for_update().get(pk=node.pk)
            metadata = dict(node.metadata) if isinstance(node.metadata, dict) else {}
            policy = metadata.get(POLICY_KEY)
            if not isinstance(policy, dict):
                return Response({"detail": "No active performance policy."}, status=409)
            try:
                metadata[STATUS_KEY] = normalize_report(
                    request.data,
                    revision=policy["revision"],
                )
            except ValidationError:
                return Response(
                    {"detail": "Invalid or stale performance report."}, status=409
                )
            node.metadata = metadata
            node.save(update_fields=["metadata"])
        return Response({"accepted": True})
