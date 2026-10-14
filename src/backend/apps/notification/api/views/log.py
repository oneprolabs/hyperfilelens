from django.utils.dateparse import parse_datetime
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.iam.permissions_org import IsOrgReader
from apps.iam.resource_access import filter_queryset_by_resource_field
from common.extension_spi import get_authz_provider

from apps.notification.api.pagination import NotificationPagination
from apps.notification.api.views._org import require_org
from apps.notification.constants import NotificationLogStatus
from apps.notification.models import NotificationChannel, NotificationLog
from apps.notification.selectors.interface import filter_logs, logs_for_org
from apps.notification.services.internal.log_details import notification_log_details


def _parse_datetime(value):
    if not value:
        return None
    parsed = parse_datetime(value)
    if not parsed:
        return None
    if timezone.is_naive(parsed):
        return timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


class NotificationLogViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, IsOrgReader]
    pagination_class = NotificationPagination
    queryset = NotificationLog.objects.all()

    def get_queryset(self):
        org = require_org(self.request)
        params = self.request.query_params
        qs = filter_logs(
            logs_for_org(organization_id=org.id),
            channel_id=params.get("channel_id", ""),
            status=params.get("status", ""),
            notification_type=params.get("notification_type", ""),
            search=params.get("search", ""),
            alert_type=params.get("type", ""),
            severity=params.get("severity", ""),
            policy_id=params.get("policy_id", ""),
        )
        start = _parse_datetime(params.get("start_time") or params.get("start_at"))
        end = _parse_datetime(params.get("end_time") or params.get("end_at"))
        if start:
            qs = qs.filter(sent_at__gte=start)
        if end:
            qs = qs.filter(sent_at__lte=end)
        provider = get_authz_provider()
        if (
            provider is not None
            and provider.has_org_action(
                self.request.user,
                org.key,
                "configuration.resources.access_all",
            )
            and provider.has_org_action(
                self.request.user, org.key, "operations.notification_records.view"
            )
        ):
            return qs
        return filter_queryset_by_resource_field(
            self.request,
            qs,
            "notification_channel",
            "channel_id",
            action="operations.notification_records.view",
        )

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        logs = list(page if page is not None else queryset)
        data = notification_log_details(logs)
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    def retrieve(self, request, *args, **kwargs):
        log = self.get_object()
        return Response(notification_log_details([log])[0])

    @action(detail=False, methods=["get"])
    def stats(self, request):
        queryset = self.filter_queryset(self.get_queryset())
        total = queryset.count()
        success = queryset.filter(status=NotificationLogStatus.SUCCESS).count()
        failed = queryset.filter(status=NotificationLogStatus.FAILED).count()
        recent_failed = queryset.filter(status=NotificationLogStatus.FAILED).order_by(
            "-sent_at"
        )[:5]
        channel_ids = list(queryset.values_list("channel_id", flat=True).distinct())
        return Response(
            {
                "total": total,
                "success": success,
                "failed": failed,
                "success_rate": round((success / total) * 100, 2) if total else 0,
                "channels": NotificationChannel.objects.filter(
                    id__in=channel_ids
                ).count(),
                "recent_failed": notification_log_details(list(recent_failed)),
            }
        )
