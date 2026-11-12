"""One delivery-log scope for lists, statistics and channel details."""

from apps.iam.activity_access import filter_activity_queryset
from apps.iam.query_authorization import request_authorization
from apps.iam.resource_access import filter_queryset_by_resource_field
from common.extension_spi import get_authz_provider


def filter_notification_logs(request, queryset):
    if get_authz_provider() is None or getattr(request, "hfl_support_readonly", False):
        return queryset
    context = request_authorization(request)
    if context is None or not context.valid:
        return queryset.none()
    queryset = context.fence_queryset(queryset.filter(organization_id=context.org.pk))
    action = "operations.notification_records.view"
    from apps.alert.models import AlertRecord

    alerts = filter_activity_queryset(
        request,
        AlertRecord.objects.filter(organization_id=context.org.pk),
        action=action,
    )
    if context.has_org_action(action) and context.has_organization_resource_scope():
        from django.db.models import Q

        task_alerts = AlertRecord.objects.filter(
            organization_id=context.org.pk, resource_type="task",
        ).values("pk")
        return queryset.filter(
            ~Q(alert_record_id__in=task_alerts)
            | Q(alert_record_id__in=alerts.values("pk")),
        )
    channels = filter_queryset_by_resource_field(
        request,
        queryset,
        "notification_channel",
        "channel_id",
        action=action,
    )
    return channels.filter(alert_record_id__in=alerts.values("pk"))
