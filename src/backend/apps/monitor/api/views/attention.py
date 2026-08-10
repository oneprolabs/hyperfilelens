"""Tenant-scoped operational attention queue."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.alert.constants import AlertStatus
from apps.alert.models import AlertRecord
from apps.audit.constants import AuditResult
from apps.audit.models import AuditLog
from apps.iam.org_context import require_org
from apps.iam.permissions_org import IsOrgReader
from apps.node.models import Node
from apps.source.constants import ResourceStatus
from apps.source.models import SourceResource
from apps.task.models import Task

KINDS = {"task", "alert", "node", "source", "audit"}
PREVIEWS = {"", "diverse"}


def _iso(value):
    return value.isoformat() if value else None


class AttentionView(APIView):
    permission_classes = [IsAuthenticated, IsOrgReader]

    def get(self, request):
        org = require_org(request)
        kind = str(request.query_params.get("type") or "").strip()
        if kind and kind not in KINDS:
            raise ValidationError({"type": "type must be one of task, alert, node, source, audit."})
        preview = str(request.query_params.get("preview") or "").strip()
        if preview not in PREVIEWS:
            raise ValidationError({"preview": "preview must be diverse when provided."})
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(100, max(1, int(request.query_params.get("page_size") or 20)))
        except ValueError as exc:
            raise ValidationError({"page": "page and page_size must be integers."}) from exc
        if preview and page != 1:
            raise ValidationError({"preview": "preview is only supported on the first page."})
        end = page * page_size
        since = timezone.now() - timedelta(hours=24)
        streams: list[tuple[int, list[dict]]] = []
        if not kind or kind == "task":
            qs = Task.objects.filter(organization_id=org.id, status=Task.Status.FAILED, finished_at__gte=since).order_by("-finished_at", "-id")
            streams.append((qs.count(), [{"id": f"task-{row.id}", "kind": "task", "title": f"Failed task: {row.display_name or row.task_type}", "detail": row.error_message or "", "occurred_at": _iso(row.finished_at or row.created_at), "to": f"/ops/task?taskUuid={row.task_uuid}"} for row in qs[:end]]))
        if not kind or kind == "alert":
            qs = AlertRecord.objects.filter(organization_id=org.id, status=AlertStatus.FIRING).order_by("-last_triggered_at", "-created_at")
            streams.append((qs.count(), [{"id": f"alert-{row.id}", "kind": "alert", "title": row.title, "detail": row.message, "occurred_at": _iso(row.last_triggered_at or row.created_at), "to": "/ops/alerts/incidents"} for row in qs[:end]]))
        if not kind or kind == "node":
            qs = Node.objects.filter(organization_id=org.id, availability=Node.Availability.OFFLINE).order_by("-availability_updated_at", "-id")
            streams.append((qs.count(), [{"id": f"node-{row.id}", "kind": "node", "title": f"Offline node: {row.name}", "detail": row.role, "occurred_at": _iso(row.availability_updated_at), "to": "/node/agents"} for row in qs[:end]]))
        if not kind or kind == "source":
            qs = SourceResource.objects.filter(organization_id=org.id, status=ResourceStatus.ERROR).order_by("-updated_at", "-id")
            streams.append((qs.count(), [{"id": f"source-{row.id}", "kind": "source", "title": f"Source error: {row.name}", "detail": row.status_message, "occurred_at": _iso(row.updated_at), "to": "/protection/backup-sources?tab=host"} for row in qs[:end]]))
        if not kind or kind == "audit":
            qs = AuditLog.objects.filter(organization_id=org.id, result=AuditResult.FAILURE).order_by("-created_at", "-id")
            streams.append((qs.count(), [{"id": f"audit-{row.id}", "kind": "audit", "title": row.action, "detail": row.error_message or row.details, "occurred_at": _iso(row.created_at), "to": "/ops/audit?result=failure"} for row in qs[:end]]))
        rows = sorted((row for _, stream in streams for row in stream), key=lambda row: row["occurred_at"] or "", reverse=True)
        if preview == "diverse":
            preview_rows: list[dict] = []
            seen_kinds: set[str] = set()
            for row in rows:
                if row["kind"] in seen_kinds:
                    continue
                preview_rows.append(row)
                seen_kinds.add(row["kind"])
            preview_rows.extend(row for row in rows if row["kind"] in seen_kinds and row not in preview_rows)
            rows = preview_rows[:page_size]
            return Response({"count": sum(count for count, _ in streams), "results": rows})
        start = (page - 1) * page_size
        return Response({"count": sum(count for count, _ in streams), "results": rows[start:end]})
