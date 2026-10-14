import csv
import json

from django.http import HttpResponse
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.api.pagination import AuditPagination
from apps.audit.api.permissions import AuditOrgPermission
from apps.audit.api.serializers import AuditLogDetailSerializer, AuditLogListSerializer
from apps.audit.models import AuditLog
from apps.audit.selectors.interface import audit_statistics, list_audit_logs
from apps.audit.services.internal.export_format import format_audit_export_row
from apps.iam.permissions_org import resolve_org_key
from apps.iam.activity_access import filter_activity_queryset


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AuditLog.objects.none()
    permission_classes = [IsAuthenticated, AuditOrgPermission]
    pagination_class = AuditPagination

    def get_serializer_class(self):
        if self.action == "list":
            return AuditLogListSerializer
        return AuditLogDetailSerializer

    def get_queryset(self):
        params = self.request.query_params
        user_id_raw = (params.get("user_id") or params.get("user") or "").strip()
        user_id = int(user_id_raw) if user_id_raw.isdigit() else None
        org_key = resolve_org_key(self.request)
        queryset = list_audit_logs(
            org_key=org_key or None,
            action=(params.get("action") or "").strip() or None,
            correlation_id=(params.get("correlation_id") or "").strip() or None,
            request_id=(params.get("request_id") or "").strip() or None,
            target_type=(params.get("target_type") or "").strip() or None,
            resource_type=(params.get("resource_type") or "").strip() or None,
            resource_id=(params.get("resource_id") or "").strip() or None,
            user_id=user_id,
            result=(params.get("result") or "").strip() or None,
            time_range=(params.get("time_range") or "").strip() or None,
            start_date=(params.get("start_date") or "").strip() or None,
            end_date=(params.get("end_date") or "").strip() or None,
            search=(params.get("search") or "").strip() or None,
            search_field=(params.get("search_field") or "").strip() or None,
            ip_address=(params.get("ip_address") or "").strip() or None,
        )
        return filter_activity_queryset(self.request, queryset, actor_field="user_id")

    @action(detail=False, methods=["get"])
    def statistics(self, request):
        org_key = resolve_org_key(request)
        visible = filter_activity_queryset(
            request, list_audit_logs(org_key=org_key or None), actor_field="user_id",
        )
        return Response(audit_statistics(org_key=org_key or None, queryset=visible))

    @action(detail=False, methods=["get"], url_path="export")
    def export_logs(self, request):
        queryset = self.filter_queryset(self.get_queryset())[:10000]
        format_type = (
            request.query_params.get("export_format")
            or request.query_params.get("file_type")
            or request.query_params.get("format")
            or "json"
        ).lower()
        rows = [format_audit_export_row(row) for row in AuditLogListSerializer(queryset, many=True).data]

        if format_type == "csv":
            response = HttpResponse(content_type="text/csv; charset=utf-8")
            response["Content-Disposition"] = (
                f'attachment; filename="audit_logs_{timezone.now().strftime("%Y%m%d")}.csv"'
            )
            writer = csv.writer(response)
            if rows:
                writer.writerow(rows[0].keys())
                for row in rows:
                    writer.writerow([row.get(k, "") for k in rows[0].keys()])
            return response

        response = HttpResponse(
            json.dumps(rows, ensure_ascii=False, indent=2),
            content_type="application/json; charset=utf-8",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="audit_logs_{timezone.now().strftime("%Y%m%d")}.json"'
        )
        return response
