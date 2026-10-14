"""Aggregate statistics for alert center dashboards."""

from __future__ import annotations

from django.db.models import Count, Q, QuerySet

from apps.alert.constants import AlertSeverity, AlertStatus
from apps.alert.models import AlertRecord
from apps.alert.selectors.interface import (
    filter_policies,
    filter_records,
    policies_for_org,
    records_for_org,
)


def _record_base_queryset(
    *, organization_id: int, **filter_kwargs
) -> QuerySet[AlertRecord]:
    qs = records_for_org(organization_id=organization_id)
    return filter_records(
        qs,
        status=filter_kwargs.get("status", ""),
        alert_type=filter_kwargs.get("alert_type", ""),
        severity=filter_kwargs.get("severity", ""),
        resource_type=filter_kwargs.get("resource_type", ""),
        resource_id=filter_kwargs.get("resource_id", ""),
        search=filter_kwargs.get("search", ""),
    )


def record_statistics(
    *,
    organization_id: int,
    search: str = "",
    severity: str = "",
    alert_type: str = "",
    resource_type: str = "",
    resource_id: str = "",
    queryset=None,
) -> dict:
    base = (
        queryset
        if queryset is not None
        else _record_base_queryset(
            organization_id=organization_id,
            search=search,
            severity=severity,
            alert_type=alert_type,
            resource_type=resource_type,
            resource_id=resource_id,
        )
    )
    active = base.exclude(status=AlertStatus.RESOLVED)
    return {
        "total": base.count(),
        "firing": base.filter(status=AlertStatus.FIRING).count(),
        "acknowledged": base.filter(status=AlertStatus.ACKNOWLEDGED).count(),
        "resolved": base.filter(status=AlertStatus.RESOLVED).count(),
        "critical": active.filter(severity=AlertSeverity.CRITICAL).count(),
        "warning": active.filter(severity=AlertSeverity.WARNING).count(),
        "info": active.filter(severity=AlertSeverity.INFO).count(),
    }


def policy_statistics(
    *,
    organization_id: int,
    search: str = "",
    alert_type: str = "",
    severity: str = "",
    resource_type: str = "",
    enabled: str = "",
    queryset=None,
) -> dict:
    qs = filter_policies(
        policies_for_org(organization_id=organization_id)
        if queryset is None
        else queryset,
        search=search,
        alert_type=alert_type,
        severity=severity,
        resource_type=resource_type,
        enabled=enabled,
    )
    agg = qs.aggregate(
        total=Count("id"),
        enabled_count=Count("id", filter=Q(enabled=True)),
        disabled_count=Count("id", filter=Q(enabled=False)),
        critical=Count("id", filter=Q(severity=AlertSeverity.CRITICAL)),
        warning=Count("id", filter=Q(severity=AlertSeverity.WARNING)),
        info=Count("id", filter=Q(severity=AlertSeverity.INFO)),
    )
    total = agg["total"] or 0
    enabled_count = agg["enabled_count"] or 0
    return {
        "total": total,
        "enabled": enabled_count,
        "disabled": agg["disabled_count"] or 0,
        "critical": agg["critical"] or 0,
        "warning": agg["warning"] or 0,
        "info": agg["info"] or 0,
        "enabled_rate": round((enabled_count / total) * 100, 2) if total else 0,
    }
