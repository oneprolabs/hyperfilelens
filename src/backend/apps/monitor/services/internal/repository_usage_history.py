from __future__ import annotations

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.monitor.models import RepositoryUsageMetric


RAW_INTERVAL = timedelta(minutes=30)
HISTORY_RETENTION_DAYS = 30
HISTORY_GRACE_PERIOD = timedelta(minutes=5)
HISTORY_RANGES = {
    "24h": (timedelta(hours=24), RAW_INTERVAL),
    "7d": (timedelta(days=7), RAW_INTERVAL),
    "14d": (timedelta(days=14), timedelta(minutes=30)),
    "15d": (timedelta(days=15), RAW_INTERVAL),
    "30d": (timedelta(days=30), RAW_INTERVAL),
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
    """Keep the latest real successful observation in its half-hour bucket."""
    slot = floor_time(recorded_at, RAW_INTERVAL)
    normalized_usage = max(0, int(usage_bytes)) if usage_bytes is not None else None
    defaults = {
        "usage_bytes": normalized_usage,
        "usage_source": (
            RepositoryUsageMetric.UsageSource.ESTIMATED
            if normalized_usage is not None
            else None
        ),
        "object_count": None,
        "observed_at": recorded_at if normalized_usage is not None else None,
    }
    if normalized_usage is None:
        # A failed attempt must not erase an observation, even when another
        # collector writes the successful sample concurrently.
        metric, _ = RepositoryUsageMetric.objects.get_or_create(
            repository=repository, recorded_at=slot, defaults=defaults,
        )
    else:
        with transaction.atomic():
            metric, _ = RepositoryUsageMetric.objects.get_or_create(
                repository=repository, recorded_at=slot, defaults=defaults,
            )
            metric = RepositoryUsageMetric.objects.select_for_update().get(pk=metric.pk)
            if metric.observed_at is None or metric.observed_at <= recorded_at:
                for name, value in defaults.items():
                    setattr(metric, name, value)
                metric.save(update_fields=list(defaults))
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
    # Do not call a staggered, still-open bucket missing before its grace expires.
    return floor_time(now - HISTORY_GRACE_PERIOD, RAW_INTERVAL) - RAW_INTERVAL


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
    # Publish real observations immediately, without declaring empty active
    # buckets missing. This also exposes a late sample in the grace window.
    current_slot = floor_time(current_time, RAW_INTERVAL)
    newest = (
        RepositoryUsageMetric.objects.filter(
            repository=repository,
            recorded_at__gt=last_raw_slot,
            recorded_at__lte=current_slot,
            usage_bytes__isnull=False,
        )
        .filter(Q(observed_at__lte=current_time) | Q(observed_at__isnull=True))
        .order_by("-recorded_at")
        .first()
    )
    if newest is not None:
        last_raw_slot = floor_time(newest.recorded_at, RAW_INTERVAL)
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
        .values("recorded_at", "observed_at", "usage_bytes", "usage_source")
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
        valid_rows = [
            row for row in bucket_rows
            if row["usage_bytes"] is not None
            and (row["observed_at"] or row["recorded_at"]) <= current_time
        ]
        latest = max(valid_rows, key=lambda row: row["observed_at"] or row["recorded_at"]) if valid_rows else None
        if not latest:
            coverage = "missing"
        elif len(valid_rows) >= eligible_slots and eligible_slots > 0:
            coverage = "complete"
        else:
            coverage = "partial"
        points.append(
            {
                "recorded_at": bucket.isoformat(),
                "sampled_at": (latest["observed_at"] or latest["recorded_at"]).isoformat() if latest else None,
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
