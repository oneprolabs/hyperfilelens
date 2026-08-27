"""Persist and query tenant resource metric samples."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from apps.monitor.models import ResourceMetric


def record_resource_metric(
    *,
    organization_id: int,
    resource_type: str,
    resource_id: str,
    metrics: dict,
    resource_name: str = "",
    source: str = ResourceMetric.Source.HEARTBEAT,
) -> ResourceMetric | None:
    if not metrics:
        return None
    return ResourceMetric.objects.create(
        organization_id=organization_id,
        resource_type=str(resource_type or ""),
        resource_id=str(resource_id or ""),
        resource_name=str(resource_name or "")[:255],
        source=source,
        metrics=metrics,
    )


def latest_resource_metric(
    *,
    organization_id: int,
    resource_type: str,
    resource_id: str,
) -> ResourceMetric | None:
    return (
        ResourceMetric.objects.filter(
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=str(resource_id),
        )
        .order_by("-timestamp")
        .first()
    )


def metrics_in_window(
    *,
    organization_id: int,
    resource_type: str,
    resource_id: str,
    since,
) -> list[ResourceMetric]:
    return list(
        ResourceMetric.objects.filter(
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=str(resource_id),
            timestamp__gte=since,
        ).order_by("timestamp")
    )


def cleanup_old_resource_metrics(*, days_to_keep: int = 14) -> int:
    cutoff = timezone.now() - timedelta(days=int(days_to_keep))
    count, _ = ResourceMetric.objects.filter(timestamp__lt=cutoff).delete()
    return count


def snapshot_repository_metrics() -> int:
    """Record capacity metrics from repository rows for alert evaluation."""
    from apps.alert.constants import ResourceType
    from apps.storage.repositories.models import Repository

    created = 0
    for repo in Repository.objects.exclude(status=Repository.Status.REMOVED).only(
        "id", "organization_id", "name", "capacity_bytes", "estimated_usage_bytes", "status"
    ):
        from apps.storage.services.internal.quota_monitoring import sync_repository_quota_policy
        sync_repository_quota_policy(repo)
        capacity = int(repo.capacity_bytes or 0)
        if capacity <= 0:
            continue
        used = int(repo.estimated_usage_bytes or 0)
        usage = round(100.0 * used / capacity, 4) if capacity else 0.0
        payload = {
            "capacity_usage": usage,
            "used_size": used,
            "free_size": max(0, capacity - used),
            "status": repo.status,
        }
        for resource_type in (
            ResourceType.BACKUP_REPOSITORY,
            ResourceType.TARGET_STORAGE,
        ):
            record_resource_metric(
                organization_id=repo.organization_id,
                resource_type=resource_type,
                resource_id=str(repo.id),
                resource_name=repo.name,
                source=ResourceMetric.Source.COLLECTOR,
                metrics=payload,
            )
            created += 1
    return created
