"""Read-only identity and readiness checks for installed-node address changes."""

import base64
import hashlib
import hmac
import json
import re

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.node.api.permissions import AllowAny
from apps.node.models import Node, NodeTask, NodeCredential
from apps.node.services.internal.node_registry import agent_ws_routable
from apps.node.services.internal.transport_identity import node_for_transport


class NodeConnectionInfoView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        node_id = str(request.query_params.get("node_id", ""))
        node = node_for_transport(
            node_id=int(node_id),
            namespace=request.headers.get("X-Org-Key", ""),
        ) if node_id.isascii() and node_id.isdigit() and len(node_id) <= 19 and 0 < int(node_id) <= 2**63 - 1 else None
        nonce = request.headers.get("X-Connection-Nonce", "")
        proof = request.headers.get("X-Connection-Proof", "")
        credential = NodeCredential.objects.filter(node=node, is_active=True).first() if node else None
        key = bytes.fromhex(credential.secret_hash) if credential else b""
        message = "\0".join(["node", nonce, str(node.id), node.transport_key, node.role, node.installation_id]) if node else ""
        expected = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
        if not credential or not re.fullmatch(r"[0-9a-f]{64}", nonce) or not re.fullmatch(r"[0-9a-f]{64}", proof) or not hmac.compare_digest(proof, expected):
            return Response({"detail": "Installed node authentication failed."}, status=401)
        busy = node.status != Node.Status.ACTIVE or NodeTask.objects.filter(
            node=node, status__in=[NodeTask.Status.PENDING, NodeTask.Status.RUNNING],
        ).exists()
        data = {
            "node_id": str(node.id),
            "org_key": node.transport_key,
            "role": node.role,
            "installation_id": node.installation_id,
            "busy": busy,
            "agent_connected": agent_ws_routable(agent_id=node.id),
            "lens": None,
        }
        if node.role == Node.Role.GATEWAY:
            from apps.lens_bridge.deploy import lens_gateway_base_path, lens_gateway_base_url
            from apps.lens_bridge.models import LensGatewayLink
            from apps.lens_bridge.services import sl_client

            link = LensGatewayLink.objects.filter(gateway=node).first()
            if link is None or not link.sl_lensnode_uuid:
                return Response({"detail": "Existing AI engine binding is unavailable."}, status=409)
            try:
                snapshot = sl_client.request_json(
                    "GET", f"/api/lens/admin/lensnodes/{link.sl_lensnode_uuid}/",
                )
            except sl_client.LensBridgeError:
                return Response({"detail": "AI engine state cannot be verified."}, status=503)
            if not isinstance(snapshot, dict) or str(snapshot.get("uuid", "")) != str(link.sl_lensnode_uuid) or not isinstance(
                snapshot.get("active_datasource_operations"), list,
            ):
                return Response({"detail": "AI engine activity state is unavailable."}, status=503)
            # SL's "tasks" describes supported capabilities, not active work.
            data["busy"] = busy or bool(
                snapshot["active_datasource_operations"]
                or snapshot.get("active_run_count")
                or snapshot.get("queued_run_count")
                or snapshot.get("awaiting_resume_count")
            )
            data["lens"] = {
                "uuid": str(link.sl_lensnode_uuid),
                "base_path": lens_gateway_base_path(),
                "base_url": lens_gateway_base_url(),
                "connected": snapshot.get("status") == "online",
                "last_authenticated_at": snapshot.get("last_authenticated_at"),
            }
        payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode()
        signature = hmac.new(key, b"server\0" + nonce.encode() + b"\0" + payload, hashlib.sha256).hexdigest()
        response = Response({"payload": base64.b64encode(payload).decode(), "proof": signature})
        response["Cache-Control"] = "no-store"
        return response
