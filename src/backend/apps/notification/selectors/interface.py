"""Notification selectors — cross-app read entry."""

from __future__ import annotations

from django.db.models import Count, QuerySet

from apps.notification.constants import NotificationLogStatus
from apps.notification.models import NotificationChannel, NotificationLog
from apps.notification.selectors.internal.history_query import filter_logs_queryset


def channels_for_org(*, organization_id: int) -> QuerySet[NotificationChannel]:
    return NotificationChannel.objects.filter(organization_id=organization_id)


def channels_by_ids(
    *,
    organization_id: int,
    channel_ids: list[int],
) -> QuerySet[NotificationChannel]:
    if not channel_ids:
        return NotificationChannel.objects.none()
    return NotificationChannel.objects.filter(
        organization_id=organization_id,
        id__in=channel_ids,
    )


def channel_summaries_for_ids(
    *,
    organization_id: int,
    channel_ids: list[int],
) -> list[dict]:
    return [
        {
            "id": ch.id,
            "name": ch.name,
            "type": ch.channel_type,
            "enabled": ch.is_active,
        }
        for ch in channels_by_ids(
            organization_id=organization_id,
            channel_ids=channel_ids,
        )
    ]


def filter_channels(
    queryset: QuerySet[NotificationChannel],
    *,
    search: str = "",
    channel_type: str = "",
    enabled: str = "",
) -> QuerySet[NotificationChannel]:
    if search:
        queryset = queryset.filter(name__icontains=search)
    if channel_type:
        queryset = queryset.filter(channel_type=channel_type)
    if enabled in {"true", "false"}:
        queryset = queryset.filter(is_active=(enabled == "true"))
    return queryset.order_by("-updated_at", "-id")


def logs_for_org(*, organization_id: int) -> QuerySet[NotificationLog]:
    return NotificationLog.objects.filter(organization_id=organization_id).select_related(
        "channel"
    )


def filter_logs(
    queryset: QuerySet[NotificationLog],
    *,
    channel_id: str = "",
    status: str = "",
    notification_type: str = "",
    search: str = "",
    alert_type: str = "",
    severity: str = "",
    policy_id: str = "",
) -> QuerySet[NotificationLog]:
    alert_record_ids = None
    if alert_type or severity or policy_id or search:
        from apps.alert.selectors.interface import alert_record_ids_matching

        alert_record_ids = list(
            alert_record_ids_matching(
                alert_type=alert_type,
                severity=severity,
                policy_id=policy_id,
                search=search,
            )
        )
    return filter_logs_queryset(
        queryset,
        channel_id=channel_id,
        status=status,
        notification_type=notification_type,
        search=search if not alert_record_ids else "",
        alert_record_ids=alert_record_ids,
    )


def failed_delivery_count() -> int:
    return NotificationLog.objects.filter(status=NotificationLogStatus.FAILED).count()


def channel_statistics(
    *,
    organization_id: int,
    search: str = "",
    channel_type: str = "",
    enabled: str = "",
    queryset=None,
) -> dict:
    from django.db.models import Q

    qs = filter_channels(
        channels_for_org(organization_id=organization_id) if queryset is None else queryset,
        search=search,
        channel_type=channel_type,
        enabled=enabled,
    )
    agg = qs.aggregate(
        total=Count("id"),
        enabled=Count("id", filter=Q(is_active=True)),
        disabled=Count("id", filter=Q(is_active=False)),
    )
    total = agg["total"] or 0
    enabled_count = agg["enabled"] or 0
    return {
        **agg,
        "enabled_rate": round((enabled_count / total) * 100, 2) if total else 0,
    }
