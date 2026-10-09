from __future__ import annotations

from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgReader
from apps.protection.services.progress.backup_runtime import build_backup_kopia_progress
from apps.task.models import Task


class BackupTaskRuntimeView(APIView):
    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request, task_uuid: str):
        org = require_org(request)
        task = Task.objects.filter(
            organization_id=org.id,
            task_uuid=task_uuid,
            task_type=Task.Type.BACKUP,
        ).first()
        if task is None:
            raise NotFound("backup task not found")
        if task.status in {Task.Status.PENDING, Task.Status.RUNNING}:
            payload = build_backup_kopia_progress(task=task)
        else:
            payload = build_backup_kopia_progress(task=task)
            result_payload = task.result_payload if isinstance(task.result_payload, dict) else {}
            transfer = result_payload.get("transfer_progress")
            if isinstance(transfer, dict):
                payload["transfer_progress"] = transfer
        return Response({
            "status": task.status,
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "progress": float(task.progress or 0),
            "transfer_progress": payload.get("transfer_progress"),
            "kopia_progress": payload,
        })
