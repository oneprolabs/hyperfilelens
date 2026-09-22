"""Gateway install status reporting from hfl-enroll gateway-install."""

from __future__ import annotations

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.models import Organization
from apps.lens_bridge.services import provisioning
from apps.node.api import permissions as node_permissions
from apps.node.models import Node
from apps.node.models.base import NodeRole
from apps.node.services.internal.agent_ws_auth import validate_agent_ws_credentials


class GatewayInstallStatusView(APIView):
    """
    Report Data Gateway install progress/failure from hfl-enroll.

    Auth: ``X-Org-Key`` + ``X-Node-Token`` (same as heartbeat).
    Body: ``node_id``, ``status`` (``failed`` | ``success``), optional ``error_message``.
    """

    permission_classes = [node_permissions.AllowAny]

    def post(self, request: Request) -> Response:
        org_key = str(request.headers.get("X-Org-Key", "") or "").strip()
        node_token = str(request.headers.get("X-Node-Token", "") or "").strip()
        payload = request.data if isinstance(request.data, dict) else {}

        node_id_raw = str(
            payload.get("node_id") or request.query_params.get("node_id") or ""
        ).strip()
        install_status = str(payload.get("status") or "").strip().lower()
        phase = str(payload.get("phase") or "install").strip().lower()
        error_message = str(
            payload.get("error_message") or payload.get("message") or ""
        ).strip()
        progress_raw = payload.get("progress")
        progress = progress_raw if isinstance(progress_raw, dict) else None

        if not org_key or not node_token or not node_id_raw or not install_status:
            return Response(
                {"error": "X-Org-Key, X-Node-Token, node_id, and status are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if install_status not in {"failed", "success", "running"}:
            return Response(
                {"error": "status must be failed, success, or running"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if phase not in {"install", "sidecar_upgrade", "sidecar_uninstall"}:
            return Response(
                {"error": "invalid phase"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            node_id = int(node_id_raw)
        except ValueError:
            return Response(
                {"error": "invalid node_id"}, status=status.HTTP_400_BAD_REQUEST
            )

        org = Organization.objects.filter(key=org_key, is_active=True).first()
        if org is None:
            return Response(
                {"error": "organization not found"}, status=status.HTTP_404_NOT_FOUND
            )

        node = Node.objects.filter(
            organization=org,
            pk=node_id,
            role=NodeRole.GATEWAY,
            is_deleted=False,
        ).first()
        if node is None:
            return Response(
                {"error": "gateway not found"}, status=status.HTTP_404_NOT_FOUND
            )
        if not validate_agent_ws_credentials(
            node.id,
            node_token,
            expected_role=NodeRole.GATEWAY,
        ):
            return Response(
                {"error": "invalid node credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        link = provisioning.record_gateway_install_status(
            org=org,
            gateway=node,
            status=install_status,
            error_message=error_message,
            phase=phase,
            progress=progress,
        )
        if link is None:
            return Response(
                {"error": "gateway lens link unavailable"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "node_id": node.id,
                "sidecar_status": link.sidecar_status,
            }
        )
