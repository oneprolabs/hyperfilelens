from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.audit.services.interface import write_audit_log
from apps.iam.permissions_org import IsOrgReader, IsOrgWriter
from apps.iam.activity_access import filter_activity_queryset
from apps.notification.channel_push import push_to_org

from apps.alert.api.pagination import AlertPagination
from apps.alert.api.serializers import (
    AlertRecordActionSerializer,
    AlertRecordSerializer,
)
from apps.alert.api.views._org import require_org
from apps.alert.constants import AlertStatus
from apps.alert.selectors.interface import filter_records, records_for_org
from apps.alert.selectors.stats import record_statistics
from apps.alert.services.internal.lifecycle import resolve_alert
from common.drf.search import parse_search_aliases


class AlertRecordViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AlertRecordSerializer
    permission_classes = [IsAuthenticated, IsOrgReader]
    pagination_class = AlertPagination

    def get_permissions(self):
        if self.action in ("acknowledge", "resolve"):
            return [IsAuthenticated(), IsOrgWriter()]
        return super().get_permissions()

    def get_queryset(self):
        org = require_org(self.request)
        qs = records_for_org(organization_id=org.id)
        params = self.request.query_params
        queryset = filter_records(
            qs,
            status=params.get("status", ""),
            alert_type=params.get("type", ""),
            severity=params.get("severity", ""),
            resource_type=params.get("resource_type", ""),
            resource_id=params.get("resource_id", ""),
            search=params.get("search", ""),
            search_aliases=parse_search_aliases(params),
            include_state_fields=params.get("search_context") == "global",
        )
        action_key = (
            self.action if self.action in ("acknowledge", "resolve") else "view"
        )
        return filter_activity_queryset(
            self.request, queryset, action=f"operations.alerts.{action_key}"
        )

    @action(detail=True, methods=["post"])
    def acknowledge(self, request, pk=None):
        alert = self.get_object()
        serializer = AlertRecordActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = timezone.now()
        if request.user.is_authenticated:
            alert.acknowledged_by = request.user.id
        metadata = dict(alert.metadata or {})
        note = serializer.validated_data.get("note")
        if note:
            metadata["acknowledge_note"] = note
        alert.metadata = metadata
        alert.save(
            update_fields=[
                "status",
                "acknowledged_at",
                "acknowledged_by",
                "metadata",
                "updated_at",
            ]
        )
        write_audit_log(
            organization=alert.organization,
            user=request.user,
            action="alert.record.acknowledge",
            target_type="alert_record",
            target_id=str(alert.id),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=str(request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={"status": alert.status},
        )
        push_to_org(
            alert.organization.key,
            {"type": "alert.update", "alert_id": str(alert.id), "status": alert.status},
        )
        return Response(self.get_serializer(alert).data)

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        alert = self.get_object()
        serializer = AlertRecordActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = serializer.validated_data.get("note")
        if note:
            metadata = dict(alert.metadata or {})
            metadata["resolve_note"] = note
            alert.metadata = metadata
            alert.save(update_fields=["metadata", "updated_at"])
        alert = resolve_alert(alert)
        write_audit_log(
            organization=alert.organization,
            user=request.user,
            action="alert.record.resolve",
            target_type="alert_record",
            target_id=str(alert.id),
            ip_address=request.META.get("REMOTE_ADDR"),
            user_agent=str(request.META.get("HTTP_USER_AGENT", "") or ""),
            metadata={"status": alert.status},
        )
        push_to_org(
            alert.organization.key,
            {"type": "alert.update", "alert_id": str(alert.id), "status": alert.status},
        )
        return Response(self.get_serializer(alert).data)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        org = require_org(request)
        params = request.query_params
        return Response(
            record_statistics(
                organization_id=org.id,
                search=params.get("search", ""),
                severity=params.get("severity", ""),
                alert_type=params.get("type", ""),
                resource_type=params.get("resource_type", ""),
                resource_id=params.get("resource_id", ""),
                queryset=self.get_queryset(),
            )
        )
