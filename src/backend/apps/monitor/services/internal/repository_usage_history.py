from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.monitor.models import RepositoryUsageMetric


RAW_INTERVAL = timedelta(minutes=15)
HISTORY_RETENTION_DAYS = 30
HISTORY_GRACE_PERIOD = timedelta(minutes=5)
HISTORY_RANGES = {
    "24h": (timedelta(hours=24), timedelta(minutes=15)),
    "7d": (timedelta(days=7), timedelta(minutes=15)),
    "14d": (timedelta(days=14), timedelta(minutes=30)),
    "15d": (timedelta(days=15), timedelta(hours=1)),
    "30d": (timedelta(days=30), timedelta(hours=1)),
}


def floor_time(value: datetime, interval: timedelta) -> datetime:
    seconds = int(interval.total_seconds())
    timestamp = int(value.timestamp())
    return datetime.fromtimestamp(timestamp - (timestamp % seconds), tz=value.tzinfo)


def record_repository_usage_result(
    repository,
    *,
    recorded_at: datetime,
    usage_bytes: int | None,
) -> RepositoryUsageMetric:
    """Upsert one repository result into its logical 15-minute slot."""
    slot = floor_time(recorded_at, RAW_INTERVAL)
    normalized_usage = max(0, int(usage_bytes)) if usage_bytes is not None else None
    metric, _ = RepositoryUsageMetric.objects.update_or_create(
        repository=repository,
        recorded_at=slot,
        defaults={
            "usage_bytes": normalized_usage,
            "usage_source": (
                RepositoryUsageMetric.UsageSource.ESTIMATED
                if normalized_usage is not None
                else None
            ),
            "object_count": None,
        },
    )
    return metric


def cleanup_repository_usage_history(
    *,
    days_to_keep: int = HISTORY_RETENTION_DAYS,
    batch_size: int = 2000,
    now: datetime | None = None,
) -> int:
    cutoff = (now or timezone.now()) - timedelta(days=max(1, int(days_to_keep)))
    deleted = 0
    while True:
        ids = list(
            RepositoryUsageMetric.objects.filter(recorded_at__lt=cutoff)
            .order_by("id")
            .values_list("id", flat=True)[: max(1, int(batch_size))]
        )
        if not ids:
            return deleted
        with transaction.atomic():
            count, _ = RepositoryUsageMetric.objects.filter(id__in=ids).delete()
        deleted += count


def _latest_mature_slot(now: datetime) -> datetime:
    current_slot = floor_time(now, RAW_INTERVAL)
    if now - current_slot < HISTORY_GRACE_PERIOD:
        return current_slot - RAW_INTERVAL
    return current_slot


def repository_usage_history_payload(
    repository,
    *,
    range_name: str,
    now: datetime | None = None,
) -> dict:
    if range_name not in HISTORY_RANGES:
        raise ValueError("range must be one of: 24h, 7d, 14d, 15d, 30d")
    current_time = now or timezone.now()
    duration, interval = HISTORY_RANGES[range_name]
    last_raw_slot = _latest_mature_slot(current_time)
    last_bucket = floor_time(last_raw_slot, interval)
    point_count = int(duration / interval)
    first_bucket = last_bucket - interval * (point_count - 1)
    raw_end = last_bucket + interval
    rows = list(
        RepositoryUsageMetric.objects.filter(
            repository=repository,
            recorded_at__gte=first_bucket,
            recorded_at__lt=raw_end,
        )
        .order_by("recorded_at")
        .values("recorded_at", "usage_bytes", "usage_source")
    )
    rows_by_bucket: dict[datetime, list[dict]] = {}
    for row in rows:
        bucket = floor_time(row["recorded_at"], interval)
        rows_by_bucket.setdefault(bucket, []).append(row)

    expected_per_bucket = int(interval / RAW_INTERVAL)
    points = []
    for index in range(point_count):
        bucket = first_bucket + interval * index
        eligible_slots = min(
            expected_per_bucket,
            max(0, int((last_raw_slot - bucket) / RAW_INTERVAL) + 1),
        )
        bucket_rows = rows_by_bucket.get(bucket, [])
        valid_rows = [row for row in bucket_rows if row["usage_bytes"] is not None]
        latest = valid_rows[-1] if valid_rows else None
        if not latest:
            coverage = "missing"
        elif len(valid_rows) >= eligible_slots and eligible_slots > 0:
            coverage = "complete"
        else:
            coverage = "partial"
        points.append(
            {
                "recorded_at": bucket.isoformat(),
                "sampled_at": latest["recorded_at"].isoformat() if latest else None,
                "usage_bytes": latest["usage_bytes"] if latest else None,
                "usage_source": latest["usage_source"] if latest else None,
                "coverage": coverage,
            }
        )

    return {
        "range": range_name,
        "start_at": first_bucket.isoformat(),
        "end_at": (last_bucket + interval).isoformat(),
        "interval": f"{int(interval.total_seconds() // 60)}m",
        "data_until": last_raw_slot.isoformat(),
        "points": points,
    }
