from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgAuthorizedMember
from apps.iam.resource_access import assert_resource_access
from apps.task.models import Task
from rest_framework.exceptions import NotFound, PermissionDenied
from apps.restore.services import interface as restore_services


def _validation_error(exc: DjangoValidationError) -> ValidationError:
    if hasattr(exc, "message_dict"):
        return ValidationError(exc.message_dict)
    return ValidationError({"detail": exc.messages})


def assert_restore_task_access(request, task, *, action="protection.restore_plans.cancel"):
    from common.extension_spi import get_authz_provider
    if get_authz_provider() is None:
        return
    from apps.restore.models import RestoreRecord
    from apps.protection.models import BackupSourceSnapshot
    payload = task.request_payload if isinstance(task.request_payload, dict) else {}
    config_id = payload.get("backup_config_id")
    if not config_id:
        record = RestoreRecord.objects.filter(
            organization_id=task.organization_id, task_id=task.pk,
        ).first()
        if record:
            config_id = record.backup_config_id
            if not config_id and record.source_snapshot_id:
                config_id = BackupSourceSnapshot.objects.filter(
                    organization_id=task.organization_id, pk=record.source_snapshot_id,
                ).values_list("backup_config_id", flat=True).first()
    if not config_id:
        raise PermissionDenied("This task has no authorized resource reference.")
    assert_resource_access(request, "backup_config", int(config_id), action=action)


class RestoreTaskCancelView(APIView):
    permission_classes = [IsAuthenticated, IsOrgAuthorizedMember]

    def post(self, request, task_uuid: str):
        org = require_org(request)
        task = Task.objects.filter(organization_id=org.pk, task_uuid=task_uuid).first()
        if task is None:
            raise NotFound("Task not found.")
        assert_restore_task_access(request, task)
        reason = str(request.data.get("reason") or "").strip()
        try:
            result = restore_services.cancel_restore(
                organization_id=org.id,
                task_uuid=str(task_uuid),
                reason=reason,
            )
        except DjangoValidationError as exc:
            raise _validation_error(exc) from exc
        return Response(result)
