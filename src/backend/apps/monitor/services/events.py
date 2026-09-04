"""Public write interface for operational events."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.monitor.models import OperationalEvent


def record_operational_event(
    *,
    organization_id: int,
    event_type: str,
    category: str,
    severity: str,
    title: str,
    details: str = "",
    occurred_at: datetime | None = None,
    resource_type: str = "",
    resource_id: str = "",
    resource_name: str = "",
    source: str = "",
    target_path: str = "",
    correlation_id: str = "",
    dedup_key: str = "",
    metadata: dict[str, Any] | None = None,
) -> OperationalEvent:
    """Persist one immutable event, returning an existing deduplicated row."""
    if category not in OperationalEvent.Category.values:
        raise ValueError("invalid operational event category")
    if severity not in OperationalEvent.Severity.values:
        raise ValueError("invalid operational event severity")
    values = {
        "event_type": event_type,
        "category": category,
        "severity": severity,
        # Resource and task names can use the full source-field width. Keep
        # the derived event headline within its own database limit.
        "title": str(title or "")[:255],
        "details": details,
        "occurred_at": occurred_at or timezone.now(),
        "resource_type": resource_type,
        "resource_id": resource_id,
        "resource_name": resource_name,
        "source": source,
        "target_path": target_path,
        "correlation_id": correlation_id,
        "metadata": metadata or {},
    }
    if dedup_key:
        event, _ = OperationalEvent.objects.get_or_create(
            organization_id=organization_id,
            dedup_key=dedup_key,
            defaults=values,
        )
        return event
    return OperationalEvent.objects.create(
        organization_id=organization_id,
        dedup_key="",
        **values,
    )


def schedule_availability_event(
    *,
    organization_id: int,
    source: str,
    availability: str,
    occurred_at: datetime,
    resource_type: str,
    resource_id: str,
    resource_name: str,
    target_path: str,
    details: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record one online/offline resource transition after commit."""
    if availability not in {"online", "offline"}:
        raise ValueError("invalid resource availability")

    def _record_event() -> None:
        record_operational_event(
            organization_id=organization_id,
            event_type=f"{source}.{availability}",
            category=OperationalEvent.Category.INFRASTRUCTURE,
            severity=(
                OperationalEvent.Severity.INFORMATION
                if availability == "online"
                else OperationalEvent.Severity.WARNING
            ),
            title=f"{resource_name} is {availability}",
            details=details,
            occurred_at=occurred_at,
            resource_type=resource_type,
            resource_id=resource_id,
            resource_name=resource_name,
            source=source,
            target_path=target_path,
            dedup_key=(
                f"{source}:{resource_id}:{availability}:{occurred_at.isoformat()}"
            ),
            metadata={"availability": availability, **(metadata or {})},
        )

    transaction.on_commit(_record_event, robust=True)


def schedule_repository_health_event(
    *,
    organization_id: int,
    repository_id: int,
    repository_name: str,
    previous_health: str,
    health: str,
    occurred_at: datetime | None = None,
) -> None:
    """Record one meaningful repository health transition after commit."""
    if health == previous_health:
        return
    severity = OperationalEvent.Severity.WARNING
    if health == "online":
        severity = OperationalEvent.Severity.INFORMATION
    elif health == "offline":
        severity = OperationalEvent.Severity.CRITICAL
    event_time = occurred_at or timezone.now()

    def _record_event() -> None:
        record_operational_event(
            organization_id=organization_id,
            event_type=f"repository.{health}",
            category=OperationalEvent.Category.INFRASTRUCTURE,
            severity=severity,
            title=f"{repository_name} is {health}",
            occurred_at=event_time,
            resource_type="repository",
            resource_id=str(repository_id),
            resource_name=repository_name,
            source="storage",
            target_path="/node/repositories",
            dedup_key=(f"repository:{repository_id}:{health}:{event_time.isoformat()}"),
            metadata={"health": health, "previous_health": previous_health},
        )

    transaction.on_commit(_record_event, robust=True)


def cleanup_operational_events(
    *,
    days_to_keep: int = 90,
    batch_size: int = 2000,
) -> int:
    """Delete expired events in bounded batches and return the deleted count."""
    cutoff = timezone.now() - timedelta(days=max(1, int(days_to_keep)))
    limit = max(1, min(10_000, int(batch_size)))
    deleted_total = 0
    while True:
        event_ids = list(
            OperationalEvent.objects.filter(occurred_at__lt=cutoff)
            .order_by("occurred_at")
            .values_list("id", flat=True)[:limit]
        )
        if not event_ids:
            return deleted_total
        deleted, _ = OperationalEvent.objects.filter(id__in=event_ids).delete()
        deleted_total += deleted
