"""Live scope for resource notifications, including already-published inbox rows."""

from django.db.models import CharField, Q
from django.db.models.functions import Cast

from apps.iam.activity_access import filter_activity_queryset
from apps.iam.resource_access import filter_resource_queryset
from common.extension_spi import get_authz_provider, get_repository_provider


def notification_source_ids(request, source_type):
    if source_type == "alert":
        from apps.alert.models import AlertRecord

        rows = filter_activity_queryset(
            request, AlertRecord.objects.all(), action="operations.alerts.view"
        )
    else:
        from apps.node.models import Node

        rows = filter_resource_queryset(
            request, Node.objects.all(), "node", action="resource.view"
        )
    return rows.annotate(_inbox_ref=Cast("pk", CharField())).values("_inbox_ref")


def filter_user_notification_queryset(request, queryset):
    # Custom repository notifications require live extension authorization,
    # even if EE was removed after the notification was created.
    provider = get_repository_provider()
    hook = getattr(provider, "filter_initialization_notifications", None)
    if callable(hook):
        queryset = hook(request, queryset)
    else:
        queryset = queryset.exclude(source_type__in=("managed_storage_owner", "managed_storage_admin"))
    if get_authz_provider() is None:
        return queryset
    condition = ~Q(source_type__in=("alert", "node"))
    for kind in ("alert", "node"):
        condition |= Q(
            source_type=kind, source_id__in=notification_source_ids(request, kind)
        )
    return queryset.filter(condition)
