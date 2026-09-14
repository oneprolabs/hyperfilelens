"""Node lifecycle operation views."""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services.interface import write_audit_log
from apps.node.api import permissions as node_permissions
from apps.node.api.serializers.node_operation import (
    NodeOperationBatchPreviewSerializer,
    NodeOperationBatchStartSerializer,
)
from apps.node.exceptions import AgentUpgradeError, NodeLifecycleError
from apps.node.services.internal.agent_uninstall import ProxyHasBoundResources
from apps.node.services.internal.node_lifecycle import (
    LIFECYCLE_KIND_UPGRADE,
    preview_batch_operations,
    start_node_remove,
    start_node_upgrade,
)
from common.drf.org_scoped import OrgScopedMixin


def _lifecycle_error_response(exc: Exception) -> Response:
    if isinstance(exc, AgentUpgradeError):
        return Response(
            {"error": str(exc), "code": exc.code},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if isinstance(exc, NodeLifecycleError):
        payload: dict = {"error": str(exc), "code": exc.code}
        if exc.blockers:
            payload["blockers"] = exc.blockers
        status_code = (
            status.HTTP_409_CONFLICT
            if exc.code in {"node_workload_active", "node_remove_blocked"}
            else status.HTTP_400_BAD_REQUEST
        )
        return Response(payload, status=status_code)
    if isinstance(exc, ProxyHasBoundResources):
        return Response(
            {
                "error": "Proxy has bound resources. Replace them before deletion.",
                "code": "proxy_has_bindings",
                "bound": exc.bindings.totals,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    raise exc


class NodeOperationBatchPreviewView(OrgScopedMixin, APIView):
    permission_classes = [
        node_permissions.IsAuthenticated,
        node_permissions.IsOrgOperator,
    ]

    def post(self, request):
        ser = NodeOperationBatchPreviewSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        body = ser.validated_data
        preview = preview_batch_operations(
            org=self.org,
            node_ids=body["node_ids"],
            kind=body["kind"],
        )
        return Response(preview)


class NodeOperationBatchStartView(OrgScopedMixin, APIView):
    permission_classes = [
        node_permissions.IsAuthenticated,
        node_permissions.IsOrgOperator,
    ]

    def post(self, request):
        ser = NodeOperationBatchStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        body = ser.validated_data
        preview = preview_batch_operations(
            org=self.org,
            node_ids=body["node_ids"],
            kind=body["kind"],
        )
        max_concurrent = int(
            body.get("max_concurrent") or preview.get("max_concurrent") or 5
        )
        eligible = preview.get("eligible") or []
        started: list[dict] = []
        errors: list[dict] = []

        for index, item in enumerate(eligible[:max_concurrent]):
            node = self._get_node(item["node_id"])
            if node is None:
                errors.append({"node_id": item["node_id"], "error": "not_found"})
                continue
            try:
                if body["kind"] == LIFECYCLE_KIND_UPGRADE:
                    result = start_node_upgrade(org=self.org, node=node, user=request.user)
                else:
                    result = start_node_remove(
                        org=self.org,
                        node=node,
                        user=request.user,
                        force=bool(body.get("force")),
                    )
                started.append(result)
                self._audit(request, node, body["kind"], result)
            except Exception as exc:
                if isinstance(exc, (NodeLifecycleError, ProxyHasBoundResources)):
                    resp = _lifecycle_error_response(exc)
                    errors.append({"node_id": node.id, **resp.data})
                else:
                    errors.append({"node_id": node.id, "error": str(exc)})

        return Response(
            {
                **preview,
                "max_concurrent": max_concurrent,
                "started": started,
                "queued": eligible[max_concurrent:],
                "errors": errors,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def _get_node(self, node_id: int):
        from apps.node.selectors.interface import get_node_for_org

        return get_node_for_org(org=self.org, node_id=node_id)

    @staticmethod
    def _audit(request, node, kind: str, result: dict) -> None:
        write_audit_log(
            organization=node.organization,
            user=request.user,
            action=f"node.lifecycle.{kind}",
            target_type="node",
            target_id=str(node.id),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=str(request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={
                "kind": kind,
                "role": node.role,
                "operation_id": result.get("operation_id"),
                "task_id": result.get("task_id"),
                "node_task_id": result.get("node_task_id"),
                "task_uuid": result.get("task_uuid"),
                "state": result.get("state"),
            },
        )
