"""Gateway LensNode config for enrolled data gateways (sidecar install)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.lens_bridge.models import LensGatewayLink
from apps.lens_bridge.services import provisioning
from apps.node.api import permissions as node_permissions
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_ws_auth import validate_agent_ws_credentials
from apps.node.services.internal.transport_identity import node_for_transport
from apps.node.services.internal.gateway_observability import (
    gateway_observability_policy,
    is_platform_gateway,
)


class GatewayLensConfigView(APIView):
    """
    Return SourceLens LensNode credentials for an enrolled gateway.

    Auth: ``X-Org-Key`` + ``X-Node-Token`` (same as heartbeat).
    Query: ``node_id`` (required after first registration).
    """

    permission_classes = [node_permissions.AllowAny]

    def get(self, request: Request) -> Response:
        org_key = str(request.headers.get("X-Org-Key", "") or "").strip()
        node_token = str(request.headers.get("X-Node-Token", "") or "").strip()
        node_id_raw = str(request.query_params.get("node_id") or "").strip()

        if not org_key or not node_token or not node_id_raw:
            return Response(
                {"error": "X-Org-Key, X-Node-Token, and node_id are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            node_id = int(node_id_raw)
        except ValueError:
            return Response(
                {"error": "invalid node_id"}, status=status.HTTP_400_BAD_REQUEST
            )

        node = node_for_transport(namespace=org_key, node_id=node_id)
        if node is None or node.role != NodeRole.GATEWAY:
            return Response(
                {"error": "gateway not found"}, status=status.HTTP_404_NOT_FOUND
            )
        expected_scope = (
            LensGatewayLink.GatewayScope.PLATFORM if is_platform_gateway(node) else None
        )
        if not validate_agent_ws_credentials(
            node.id,
            node_token,
            expected_role=NodeRole.GATEWAY,
            expected_gateway_scope=expected_scope,
        ):
            return Response(
                {"error": "invalid node credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        lens = provisioning.provision_gateway_lens_on_register(org=node.organization, gateway=node)
        if lens is None:
            return Response(
                {
                    "error": "SourceLens bridge is not configured or lens provisioning failed"
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not lens.get("lensnode_token"):
            return Response(
                {"error": "LensNode token unavailable"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        response = Response(
            {
                "node_id": node.id,
                "gateway_scope": "public" if is_platform_gateway(node) else "private",
                "lens": lens,
                "observability": gateway_observability_policy(node),
            }
        )
        response["Cache-Control"] = "no-store"
        return response
