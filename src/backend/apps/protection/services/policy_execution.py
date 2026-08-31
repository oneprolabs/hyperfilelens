from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone

from apps.protection.models import BackupConfig, BackupPolicy, BackupSourceSnapshot
from apps.protection.services.backup_task import start_backup_tasks
from apps.protection.services.snapshot_delete import create_and_queue_snapshot_delete_task
from apps.task.models import Task


@dataclass(frozen=True)
class PolicyExecutionSummary:
    scheduled: int = 0
    retention_tasks: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scheduled": self.scheduled,
            "retention_tasks": self.retention_tasks,
            "skipped": self.skipped,
        }


def _cron_value_matches(field: str, value: int) -> bool:
    field = str(field or "").strip()
    if field == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        step = 1
        base = part
        if "/" in part:
            base, raw_step = part.split("/", 1)
            try:
                step = max(1, int(raw_step))
            except (TypeError, ValueError):
                return False
        if base == "*":
            return value % step == 0
        if "-" in base:
            start_raw, end_raw = base.split("-", 1)
            try:
                start = int(start_raw)
                end = int(end_raw)
            except (TypeError, ValueError):
                return False
            if start <= value <= end and (value - start) % step == 0:
                return True
            continue
        try:
            if int(base) == value:
                return True
        except (TypeError, ValueError):
            return False
    return False


def _aware_minute(now=None):
    current = now or timezone.now()
    if timezone.is_naive(current):
        current = timezone.make_aware(current, UTC)
    return current.replace(second=0, microsecond=0)


def _schedule_timezone(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(timezone_name or "UTC"))
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _schedule_local_minute(schedule: dict, *, now=None):
    return _aware_minute(now).astimezone(_schedule_timezone(schedule.get("timezone")))


def cron_matches_now(cron_expr: str, *, now=None, timezone_name: str = "UTC") -> bool:
    current = _aware_minute(now).astimezone(_schedule_timezone(timezone_name))
    fields = str(cron_expr or "").split()
    if len(fields) != 5:
        return False
    minute, hour, day_of_month, month, day_of_week = fields
    # Python weekday: Monday=0; cron commonly accepts Sunday=0. This maps Sunday to 0.
    cron_weekday = 0 if current.weekday() == 6 else current.weekday() + 1
    weekday_matches = _cron_value_matches(day_of_week, cron_weekday) or (
        cron_weekday == 0 and _cron_value_matches(day_of_week, 7)
    )
    return (
        _cron_value_matches(minute, current.minute)
        and _cron_value_matches(hour, current.hour)
        and _cron_value_matches(day_of_month, current.day)
        and _cron_value_matches(month, current.month)
        and weekday_matches
    )


def _schedule_start(schedule: dict) -> datetime | None:
    raw = str(schedule.get("starts_at") or "").strip()
    if not raw:
        return None
    for value_format in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(raw, value_format)
        except ValueError:
            continue
    return None


def _schedule_start_minute(schedule: dict) -> datetime | None:
    starts_at = _schedule_start(schedule)
    if starts_at is None:
        return None
    if starts_at.second or starts_at.microsecond:
        starts_at += timedelta(minutes=1)
    return starts_at.replace(second=0, microsecond=0)


def _schedule_start_instant(schedule: dict) -> datetime | None:
    starts_at = _schedule_start_minute(schedule)
    if starts_at is None:
        return None
    return starts_at.replace(
        tzinfo=_schedule_timezone(schedule.get("timezone"))
    ).astimezone(UTC)


def _schedule_has_started(schedule: dict, current) -> bool:
    starts_at = _schedule_start_minute(schedule)
    if starts_at is None:
        return True
    return current.replace(tzinfo=None) >= starts_at


def _schedule_time_matches(schedule: dict, current) -> bool:
    raw = str(schedule.get("time") or "")
    try:
        hour, minute = (int(part) for part in raw.split(":", 1))
    except (TypeError, ValueError):
        return False
    return current.hour == hour and current.minute == minute


def schedule_matches_now(schedule: dict, *, now=None) -> bool:
    if not isinstance(schedule, dict) or not schedule.get("enabled", False):
        return False
    mode = str(schedule.get("mode") or "").strip().lower()
    if not mode:
        return cron_matches_now(str(schedule.get("cron_expr") or ""), now=now)

    current = _schedule_local_minute(schedule, now=now)
    if not _schedule_has_started(schedule, current):
        return False
    if mode == "advanced":
        return cron_matches_now(
            str(schedule.get("cron_expr") or ""),
            now=now,
            timezone_name=str(schedule.get("timezone") or "UTC"),
        )
    if mode == "interval":
        starts_at = _schedule_start_minute(schedule)
        if starts_at is None:
            return cron_matches_now(
                str(schedule.get("cron_expr") or ""),
                now=now,
                timezone_name=str(schedule.get("timezone") or "UTC"),
            )
        unit_minutes = {"minute": 1, "hour": 60, "day": 24 * 60}
        try:
            interval_value = int(schedule.get("interval_value") or 0)
        except (TypeError, ValueError):
            return False
        interval_minutes = unit_minutes.get(str(schedule.get("interval_unit") or ""), 0) * interval_value
        if interval_minutes < 1:
            return False
        start_instant = _schedule_start_instant(schedule)
        if start_instant is None:
            return False
        elapsed_minutes = int((_aware_minute(now) - start_instant).total_seconds() // 60)
        return elapsed_minutes >= 0 and elapsed_minutes % interval_minutes == 0
    if not _schedule_time_matches(schedule, current):
        return False
    if mode == "daily":
        return True
    if mode == "weekly":
        try:
            weekdays = {int(day) for day in schedule.get("weekdays", [])}
        except (TypeError, ValueError):
            return False
        return current.isoweekday() in weekdays
    if mode == "monthly":
        try:
            month_days = {int(day) for day in schedule.get("month_days", [])}
        except (TypeError, ValueError):
            return False
        is_month_end = current.day == calendar.monthrange(current.year, current.month)[1]
        return current.day in month_days or (bool(schedule.get("month_end")) and is_month_end)
    return False


def _schedule_fire_key(schedule: dict, *, now=None) -> str:
    if schedule.get("mode") and schedule.get("mode") != "interval":
        current = _schedule_local_minute(schedule, now=now)
    else:
        current = _aware_minute(now).astimezone(ZoneInfo("UTC"))
    # Calendar schedules deliberately omit the UTC offset so a repeated DST
    # wall-clock minute is one logical occurrence. Fixed intervals use UTC
    # because both repeated wall-clock minutes are separate elapsed intervals.
    return current.strftime("%Y%m%d%H%M")


def _policy_configs() -> list[tuple[BackupConfig, BackupPolicy]]:
    configs = list(
        BackupConfig.objects.exclude(backup_policy_id__isnull=True)
        .exclude(backup_policy_id=0)
        .order_by("organization_id", "id")
    )
    policy_ids = {int(config.backup_policy_id) for config in configs if config.backup_policy_id}
    policies = {
        int(policy.id): policy
        for policy in BackupPolicy.objects.filter(id__in=policy_ids, is_active=True)
    }
    return [
        (config, policies[int(config.backup_policy_id)])
        for config in configs
        if config.backup_policy_id and int(config.backup_policy_id) in policies
    ]


def schedule_due_backup_tasks(*, now=None) -> dict[str, int]:
    current = _aware_minute(now)
    scheduled = 0
    skipped = 0
    for config, policy in _policy_configs():
        schedule = policy.schedule if isinstance(policy.schedule, dict) else {}
        if not schedule_matches_now(schedule, now=current):
            skipped += 1
            continue
        fire_key = _schedule_fire_key(schedule, now=current)
        result = start_backup_tasks(
            organization_id=config.organization_id,
            sources=[
                {
                    "source_type": config.source_type,
                    "source_ref_id": config.source_ref_id,
                }
            ],
            backup_config_ids=[config.id],
            trigger_type=BackupSourceSnapshot.TriggerType.SCHEDULE,
            idempotency_key=f"schedule:{policy.id}:{config.id}:{fire_key}",
        )
        scheduled += int(result.get("created_count") or 0)
        skipped += int(result.get("skipped_count") or 0)
    return {"scheduled": scheduled, "skipped": skipped}


def _snapshot_time(snapshot: BackupSourceSnapshot):
    return snapshot.finished_at or snapshot.started_at or snapshot.created_at


def _bucket_key(snapshot: BackupSourceSnapshot, unit: str):
    value = timezone.localtime(_snapshot_time(snapshot))
    if unit == "hour":
        return value.strftime("%Y%m%d%H")
    if unit == "day":
        return value.strftime("%Y%m%d")
    if unit == "week":
        iso = value.isocalendar()
        return f"{iso.year}W{iso.week:02d}"
    if unit == "month":
        return value.strftime("%Y%m")
    if unit == "year":
        return value.strftime("%Y")
    return str(snapshot.id)


def _apply_bucket_retention(
    *,
    keep_ids: set[int],
    snapshots: list[BackupSourceSnapshot],
    now,
    enabled: bool,
    amount: int,
    unit: str,
    delta: timedelta,
) -> None:
    if not enabled or amount < 1:
        return
    cutoff = now - delta
    seen: set[str] = set()
    for snapshot in snapshots:
        if _snapshot_time(snapshot) < cutoff:
            continue
        key = _bucket_key(snapshot, unit)
        if key in seen:
            continue
        seen.add(key)
        keep_ids.add(int(snapshot.id))


def retention_delete_candidates_for_config(
    *,
    config: BackupConfig,
    policy: BackupPolicy,
    now=None,
) -> list[BackupSourceSnapshot]:
    retention = policy.retention if isinstance(policy.retention, dict) else {}
    if not retention.get("enabled", False):
        return []
    snapshots = list(
        BackupSourceSnapshot.objects.filter(
            organization_id=config.organization_id,
            source_type=config.source_type,
            source_ref_id=config.source_ref_id,
            backup_config_id=config.id,
            deleted_at__isnull=True,
            status__in=[
                BackupSourceSnapshot.Status.AVAILABLE,
                BackupSourceSnapshot.Status.PARTIAL,
            ],
        ).order_by("-finished_at", "-created_at", "-id")
    )
    if len(snapshots) <= 1:
        return []
    current = timezone.localtime(now or timezone.now())
    keep_ids = {int(snapshot.id) for snapshot in snapshots[: max(1, int(retention.get("recent_points") or 1))]}
    _apply_bucket_retention(
        keep_ids=keep_ids,
        snapshots=snapshots,
        now=current,
        enabled=bool(retention.get("hourly_enabled", False)),
        amount=int(retention.get("hourly_hours") or 0),
        unit="hour",
        delta=timedelta(hours=max(1, int(retention.get("hourly_hours") or 1))),
    )
    _apply_bucket_retention(
        keep_ids=keep_ids,
        snapshots=snapshots,
        now=current,
        enabled=bool(retention.get("daily_enabled", False)),
        amount=int(retention.get("daily_days") or 0),
        unit="day",
        delta=timedelta(days=max(1, int(retention.get("daily_days") or 1))),
    )
    _apply_bucket_retention(
        keep_ids=keep_ids,
        snapshots=snapshots,
        now=current,
        enabled=bool(retention.get("weekly_enabled", False)),
        amount=int(retention.get("weekly_weeks") or 0),
        unit="week",
        delta=timedelta(weeks=max(1, int(retention.get("weekly_weeks") or 1))),
    )
    _apply_bucket_retention(
        keep_ids=keep_ids,
        snapshots=snapshots,
        now=current,
        enabled=bool(retention.get("monthly_enabled", False)),
        amount=int(retention.get("monthly_months") or 0),
        unit="month",
        delta=timedelta(days=31 * max(1, int(retention.get("monthly_months") or 1))),
    )
    _apply_bucket_retention(
        keep_ids=keep_ids,
        snapshots=snapshots,
        now=current,
        enabled=bool(retention.get("annual_enabled", False)),
        amount=int(retention.get("annual_years") or 0),
        unit="year",
        delta=timedelta(days=366 * max(1, int(retention.get("annual_years") or 1))),
    )
    return [snapshot for snapshot in snapshots if int(snapshot.id) not in keep_ids]


def failed_snapshot_delete_candidates_for_config(
    *,
    config: BackupConfig,
    policy: BackupPolicy,
) -> list[BackupSourceSnapshot]:
    retention = policy.retention if isinstance(policy.retention, dict) else {}
    if not retention.get("enabled", False):
        return []
    return list(
        BackupSourceSnapshot.objects.filter(
            organization_id=config.organization_id,
            source_type=config.source_type,
            source_ref_id=config.source_ref_id,
            backup_config_id=config.id,
            deleted_at__isnull=True,
            status=BackupSourceSnapshot.Status.FAILED,
        ).order_by("finished_at", "created_at", "id")
    )


def apply_retention_policies(*, now=None, limit: int = 100) -> dict[str, int]:
    created = 0
    skipped = 0
    for config, policy in _policy_configs():
        for snapshot in failed_snapshot_delete_candidates_for_config(
            config=config,
            policy=policy,
        )[: max(1, int(limit))]:
            with transaction.atomic():
                task = create_and_queue_snapshot_delete_task(
                    source_snapshot=snapshot,
                    trigger_type=Task.TriggerType.SYSTEM,
                )
            if task.status in {Task.Status.PENDING, Task.Status.RUNNING}:
                created += 1
            else:
                skipped += 1
        for snapshot in retention_delete_candidates_for_config(
            config=config,
            policy=policy,
            now=now,
        )[: max(1, int(limit))]:
            with transaction.atomic():
                task = create_and_queue_snapshot_delete_task(
                    source_snapshot=snapshot,
                    trigger_type=Task.TriggerType.SYSTEM,
                )
            if task.status in {Task.Status.PENDING, Task.Status.RUNNING}:
                created += 1
            else:
                skipped += 1
    return {"retention_tasks": created, "skipped": skipped}


def run_backup_policy_maintenance(*, now=None, retention_limit: int = 100) -> dict[str, int]:
    scheduled = schedule_due_backup_tasks(now=now)
    retention = apply_retention_policies(now=now, limit=retention_limit)
    return {
        "scheduled": int(scheduled.get("scheduled") or 0),
        "retention_tasks": int(retention.get("retention_tasks") or 0),
        "skipped": int(scheduled.get("skipped") or 0) + int(retention.get("skipped") or 0),
    }
