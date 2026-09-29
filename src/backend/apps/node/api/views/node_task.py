"""REST views for ``NodeTask`` lifecycle."""

import logging

from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.audit.services.interface import write_audit_log
from apps.iam.permissions_org import resolve_org_key
from apps.node.api import permissions as node_permissions
from apps.node.api.serializers import NodeTaskDispatchSerializer, NodeTaskSerializer
from apps.node.exceptions import AgentUpgradeError
from apps.node.models import NodeTask
from apps.node.selectors.interface import get_node_for_org, get_node_task_runtime_info
from apps.node.services.internal.agent_upgrade import (
    is_agent_upgrade_kind,
    validate_agent_upgrade,
)
from apps.node.services.interface import (
    cancel_agent_task,
    run_agent_task_async,
    wait_for_agent_task,
)
from apps.storage.services.internal.repository_secrets import scrub_secrets
from common.drf.org_scoped import OrgScopedMixin

logger = logging.getLogger(__name__)


class NodeTaskViewSet(OrgScopedMixin, viewsets.ModelViewSet):
    """
    Console lifecycle for runtime tasks: list/retrieve/create(dispatch)/cancel/wait.
    """

    queryset = NodeTask.objects.select_related("organization", "node").all()
    serializer_class = NodeTaskSerializer
    http_method_names = ["get", "post", "head", "options"]

    def get_permissions(self):
        if self.action in ("list", "retrieve", "wait"):
            return [
                node_permissions.IsAuthenticated(),
                node_permissions.IsOrgStaffReader(),
            ]
        return [
            node_permissions.IsAuthenticated(),
            node_permissions.IsOrgOperator(),
        ]

    def get_org_scoped_queryset(self):
        return self.queryset.order_by("-created_at", "-id")

    def create(self, request, *args, **kwargs):
        ser = NodeTaskDispatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        body = ser.validated_data

        org = self.org
        hdr_org = resolve_org_key(request).strip()
        body_org_key = (body.get("org_key") or "").strip()
        if body_org_key and hdr_org and hdr_org != body_org_key:
            return Response(
                {"error": "X-Org-Key must match org_key"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if body_org_key and body_org_key != org.key:
            raise ValidationError({"org_key": "Organization key does not match active context."})

        node = get_node_for_org(org=org, node_id=body["node_id"])
        if node is None:
            return Response(
                {"error": "node not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        kind = str(body["kind"] or "").strip()
        if is_agent_upgrade_kind(kind):
            try:
                validate_agent_upgrade(node=node)
            except AgentUpgradeError as exc:
                return Response(
                    {"error": str(exc), "code": exc.code},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        handle = run_agent_task_async(
            org=org,
            node_id=node.id,
            kind=kind,
            payload=body.get("payload") or {},
            correlation_type=body.get("correlation_type") or "",
            correlation_id=body.get("correlation_id") or "",
        )
        task = handle.task
        logger.info(
            "node task dispatch ok node_id=%s kind=%s task_id=%s correlation_type=%s correlation_id=%s user_id=%s",
            node.id,
            kind,
            task.pk,
            body.get("correlation_type") or "",
            body.get("correlation_id") or "",
            getattr(request.user, "id", None),
        )

        write_audit_log(
            organization=org,
            user=request.user,
            action="node_task.dispatch",
            target_type="node_task",
            target_id=str(task.pk),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=str(request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={
                "kind": task.kind,
                "status": task.status,
                "node_id": task.node_id,
            },
        )

        return Response(
            NodeTaskSerializer(task).data,
            status=status.HTTP_202_ACCEPTED,
        )

    def retrieve(self, request, *args, **kwargs):
        task = self.get_object()
        data = NodeTaskSerializer(task).data
        data["redis"] = scrub_secrets(get_node_task_runtime_info(task_id=str(task.id)))
        return Response(data)

    @action(detail=True, methods=["get"], url_path="wait")
    def wait(self, request, pk=None):
        task = self.get_object()
        timeout = int(request.query_params.get("timeout", "15"))
        outcome = wait_for_agent_task(task_id=task.id, timeout_seconds=timeout)
        return Response(
            {
                "task_id": str(outcome.task.id),
                "status": outcome.task.status,
                "timed_out": outcome.timed_out,
                "message": scrub_secrets(outcome.stream_message),
                "result": scrub_secrets(outcome.result),
                "error_details": NodeTaskSerializer(outcome.task).data.get("error_details"),
            }
        )

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        task = self.get_object()
        reason = str(request.data.get("reason") or "canceled by operator")
        canceled = cancel_agent_task(task_id=task.id, reason=reason)
        return Response(
            {
                "task_id": str(task.pk),
                "status": canceled.status if canceled else "unknown",
            }
        )
