"""Alert selectors."""

from __future__ import annotations

from django.db.models import Q, QuerySet

from apps.alert.models import AlertPolicy, AlertRecord
from apps.iam.models import Organization
from apps.notification.selectors.interface import channel_summaries_for_ids
from common.drf.search import text_search_query


def policies_for_org(*, organization_id: int) -> QuerySet[AlertPolicy]:
    from common.resource_scope import ResourceScope

    return AlertPolicy.objects.filter(
        owner_scope=ResourceScope.ORGANIZATION,
        organization_id=organization_id,
    )


def policies_for_platform() -> QuerySet[AlertPolicy]:
    from common.resource_scope import ResourceScope

    return AlertPolicy.objects.filter(
        owner_scope=ResourceScope.PLATFORM,
        organization__isnull=True,
    )


def records_for_org(*, organization_id: int) -> QuerySet[AlertRecord]:
    from common.resource_scope import ResourceScope

    return AlertRecord.objects.filter(
        owner_scope=ResourceScope.ORGANIZATION,
        organization_id=organization_id,
    )


def records_for_platform() -> QuerySet[AlertRecord]:
    from common.resource_scope import ResourceScope

    return AlertRecord.objects.filter(
        owner_scope=ResourceScope.PLATFORM,
        organization__isnull=True,
    )


def filter_policies(
    queryset: QuerySet[AlertPolicy],
    *,
    search: str = "",
    alert_type: str = "",
    severity: str = "",
    resource_type: str = "",
    enabled: str = "",
) -> QuerySet[AlertPolicy]:
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search) | Q(description__icontains=search)
        )
    if alert_type:
        queryset = queryset.filter(type=alert_type)
    if severity:
        queryset = queryset.filter(severity=severity)
    if resource_type:
        queryset = queryset.filter(resource_type=resource_type)
    if enabled in {"true", "false"}:
        queryset = queryset.filter(enabled=(enabled == "true"))
    return queryset.order_by("-updated_at", "-created_at")


def filter_records(
    queryset: QuerySet[AlertRecord],
    *,
    status: str = "",
    alert_type: str = "",
    severity: str = "",
    resource_type: str = "",
    resource_id: str = "",
    search: str = "",
    search_aliases: tuple[str, ...] = (),
    include_state_fields: bool = False,
) -> QuerySet[AlertRecord]:
    if status == "all":
        status = ""
    if status:
        queryset = queryset.filter(status=status)
    if alert_type:
        queryset = queryset.filter(type=alert_type)
    if severity:
        queryset = queryset.filter(severity=severity)
    if resource_type:
        queryset = queryset.filter(resource_type=resource_type)
    if resource_id:
        queryset = queryset.filter(resource_id=resource_id)
    if search:
        fields = ("title", "message", "resource_name")
        if include_state_fields:
            fields += ("id", "status", "severity")
        queryset = queryset.filter(
            text_search_query(search, fields, search_aliases)
        )
    return queryset.order_by("-last_triggered_at", "-created_at")


def notification_channels_for_policy(
    policy: AlertPolicy,
    org: Organization | None,
) -> list[dict]:
    from common.resource_scope import ResourceScope
    from apps.notification.selectors.interface import platform_channels_by_ids

    ids = policy.notification_channel_ids or []
    int_ids: list[int] = []
    for raw in ids:
        try:
            int_ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    if policy.owner_scope == ResourceScope.PLATFORM or org is None:
        return [
            {
                "id": ch.id,
                "name": ch.name,
                "type": ch.channel_type,
                "enabled": ch.is_active,
            }
            for ch in platform_channels_by_ids(channel_ids=int_ids)
        ]
    return channel_summaries_for_ids(organization_id=org.id, channel_ids=int_ids)


def alert_record_ids_matching(
    *,
    alert_type: str = "",
    severity: str = "",
    policy_id: str = "",
    search: str = "",
    search_aliases: tuple[str, ...] = (),
) -> QuerySet:
    queryset = AlertRecord.objects.all()
    if alert_type:
        queryset = queryset.filter(type=alert_type)
    if severity:
        queryset = queryset.filter(severity=severity)
    if policy_id:
        queryset = queryset.filter(policy_id=policy_id)
    if search:
        queryset = queryset.filter(
            text_search_query(search, ("title", "message", "resource_name"), search_aliases)
        )
    return queryset.values_list("id", flat=True)


def platform_alert_counts() -> dict[str, int]:
    from apps.alert.constants import AlertStatus

    return {
        "firing": AlertRecord.objects.filter(status=AlertStatus.FIRING).count(),
        "acknowledged": AlertRecord.objects.filter(status=AlertStatus.ACKNOWLEDGED).count(),
    }


def recent_active_alerts(*, limit: int = 10) -> list[AlertRecord]:
    from apps.alert.constants import AlertStatus

    return list(
        AlertRecord.objects.filter(
            status__in=[AlertStatus.FIRING, AlertStatus.ACKNOWLEDGED]
        )
        .select_related("organization")
        .order_by("-last_triggered_at", "-created_at")[:limit]
    )
