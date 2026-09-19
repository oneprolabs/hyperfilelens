from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.resource_access import assert_resource_access
from apps.iam.permissions_org import IsOrgOperator
from apps.protection.api.serializers.backup_task import (
    RetryBackupDirectorySerializer,
    StartBackupTaskSerializer,
)
from apps.protection.services.backup_orchestrator import cancel_backup, retry_backup_directory
from apps.protection.services.backup_task import start_backup_tasks
from apps.task.models import Task
from apps.source.services.internal.selectable_ids import parse_selectable_id


def _validation_error(exc: DjangoValidationError) -> ValidationError:
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"detail": exc.messages})


def _assert_backup_request_access(request, *, sources, source_ids, config_ids):
    """Check every source/config before a backup operation is admitted."""
    for source in sources or []:
        resource_type = (
            "node" if source["source_type"] == "agent" else "source_resource"
        )
        assert_resource_access(
            request,
            resource_type,
            source["source_ref_id"],
            action="resources.manage",
        )
    for raw_id in source_ids or []:
        parsed = parse_selectable_id(str(raw_id))
        if parsed is None or parsed[0] == "proxy":
            continue
        resource_type = "node" if parsed[0] == "agent" else "source_resource"
        assert_resource_access(
            request,
            resource_type,
            parsed[1],
            action="resources.manage",
        )
    for config_id in config_ids or []:
        assert_resource_access(
            request,
            "backup_config",
            config_id,
            action="resources.manage",
        )


def _assert_task_resource_access(request, task: Task) -> None:
    payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    config_id = payload.get("backup_config_id")
    if config_id:
        assert_resource_access(
            request,
            "backup_config",
            int(config_id),
            action="resources.manage",
        )
        return
    source_type = str(payload.get("source_type") or "")
    source_ref_id = payload.get("source_ref_id")
    if source_ref_id and source_type in {"agent", "nas"}:
        assert_resource_access(
            request,
            "node" if source_type == "agent" else "source_resource",
            int(source_ref_id),
            action="resources.manage",
        )


class BackupTaskStartView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request):
        org = require_org(request)
        serializer = StartBackupTaskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _assert_backup_request_access(
            request,
            sources=serializer.validated_data.get("sources"),
            source_ids=serializer.validated_data.get("source_ids"),
            config_ids=serializer.validated_data.get("backup_config_ids"),
        )
        try:
            result = start_backup_tasks(
                organization_id=org.id,
                sources=serializer.validated_data.get("sources"),
                source_ids=serializer.validated_data.get("source_ids"),
                backup_config_ids=serializer.validated_data.get("backup_config_ids"),
                trigger_type=serializer.validated_data.get("trigger_type") or "manual",
                idempotency_key=serializer.validated_data.get("idempotency_key"),
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(result, status=status.HTTP_201_CREATED)


class BackupTaskCancelView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request, task_uuid: str):
        org = require_org(request)
        task = Task.objects.filter(
            organization_id=org.id,
            task_uuid=task_uuid,
        ).first()
        if task is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("task not found")
        _assert_task_resource_access(request, task)
        try:
            result = cancel_backup(organization_id=org.id, task_uuid=str(task_uuid))
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(result)


class BackupTaskRetryDirectoryView(APIView):
    permission_classes = [IsAuthenticated, IsOrgOperator]

    def post(self, request, task_uuid: str):
        org = require_org(request)
        serializer = RetryBackupDirectorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        task = Task.objects.filter(
            organization_id=org.id,
            task_uuid=task_uuid,
        ).first()
        if task is None:
            from rest_framework.exceptions import NotFound

            raise NotFound("task not found")
        _assert_task_resource_access(request, task)
        try:
            result = retry_backup_directory(
                organization_id=org.id,
                task_uuid=str(task_uuid),
                backup_config_dir_id=int(serializer.validated_data["backup_config_dir_id"]),
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(result)
