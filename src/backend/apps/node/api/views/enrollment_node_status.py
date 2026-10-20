"""Authenticated post-install node verification for the enrollment helper."""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.node.api import permissions as node_permissions
from apps.node.services.internal.agent_ws_auth import validate_agent_ws_credentials
from apps.node.services.internal.transport_identity import node_for_transport
from apps.node.services.internal.node_registry import (
    agent_connection_status,
    agent_ws_routable,
)


class EnrollmentNodeStatusView(APIView):
    """Return current service-independent control-plane connectivity."""

    permission_classes = [node_permissions.AllowAny]

    def get(self, request):
        org_key = str(request.headers.get("X-Org-Key", "") or "").strip()
        credential = str(request.headers.get("X-Node-Token", "") or "").strip()
        node_id = str(request.query_params.get("node_id") or "").strip()
        if not org_key or not credential or not node_id.isascii() or not node_id.isdigit():
            return Response(
                {"error": "X-Org-Key, X-Node-Token, and node_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(node_id) > 19 or not 0 < int(node_id) <= 9_223_372_036_854_775_807:
            return Response(
                {"error": "node_id is invalid"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        node = node_for_transport(namespace=org_key, node_id=int(node_id))
        if node is None:
            return Response(
                {"error": "node not found"}, status=status.HTTP_404_NOT_FOUND
            )
        if not validate_agent_ws_credentials(node.id, credential):
            return Response(
                {"error": "invalid node credential"},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(
            {
                "node_id": node.id,
                "status": agent_connection_status(node),
                "routable": agent_ws_routable(agent_id=node.id),
            }
        )
