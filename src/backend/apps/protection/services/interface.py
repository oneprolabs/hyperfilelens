from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.protection.models import BackupConfig, BackupPolicy, FileFilterRule

CRON_FIELD_RE = re.compile(r"^(\*|\d+(-\d+)?)(/\d+)?(,(\*|\d+(-\d+)?)(/\d+)?)*$")
SCHEDULE_MODES = {"interval", "daily", "weekly", "monthly", "advanced"}
SCHEDULE_INTERVAL_LIMITS = {"minute": 59, "hour": 23, "day": 365}
SCHEDULE_LOCAL_DATETIME_FORMAT = "%Y-%m-%dT%H:%M"
SCHEDULE_TIME_FORMAT = "%H:%M"


class ResourceInUseError(Exception):
    pass


@dataclass(frozen=True)
class BulkFailure:
    id: int
    reason: str


def normalize_ignore_patterns(raw: str | None) -> str:
    lines = [line.strip() for line in str(raw or "").splitlines()]
    return "\n".join(line for line in lines if line)


def validate_cron_expr(raw: str) -> str:
    cron_expr = str(raw or "").strip()
    if not cron_expr:
        raise ValidationError({"schedule": "cron_expr is required."})
    fields = cron_expr.split()
    if len(fields) != 5 or any(not CRON_FIELD_RE.match(field) for field in fields):
        raise ValidationError({"schedule": "cron_expr must be a valid 5-field cron expression."})
    return cron_expr


def _normalize_schedule_timezone(raw: Any) -> str:
    timezone_name = str(raw or "UTC").strip() or "UTC"
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError({"schedule": "timezone must be a valid IANA timezone."}) from exc
    return timezone_name


def _normalize_schedule_starts_at(raw: Any, timezone_name: str) -> str | None:
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    try:
        parsed = datetime.strptime(text, SCHEDULE_LOCAL_DATETIME_FORMAT)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            {"schedule": "starts_at must use YYYY-MM-DDTHH:MM in the selected timezone."}
        ) from exc
    schedule_timezone = ZoneInfo(timezone_name)
    round_tripped = (
        parsed.replace(tzinfo=schedule_timezone)
        .astimezone(UTC)
        .astimezone(schedule_timezone)
        .replace(tzinfo=None)
    )
    if round_tripped != parsed:
        raise ValidationError(
            {"schedule": "starts_at does not exist in the selected timezone because of DST."}
        )
    return parsed.strftime(SCHEDULE_LOCAL_DATETIME_FORMAT)


def _normalize_schedule_time(raw: Any) -> str:
    text = str(raw or "").strip()
    try:
        parsed = datetime.strptime(text, SCHEDULE_TIME_FORMAT)
    except (TypeError, ValueError) as exc:
        raise ValidationError({"schedule": "time must use HH:MM."}) from exc
    return parsed.strftime(SCHEDULE_TIME_FORMAT)


def _schedule_time_parts(value: str) -> tuple[int, int]:
    parsed = datetime.strptime(value, SCHEDULE_TIME_FORMAT)
    return parsed.hour, parsed.minute


def _interval_cron(unit: str, value: int) -> str:
    if unit == "minute":
        return f"*/{value} * * * *"
    if unit == "hour":
        return f"0 */{value} * * *"
    return f"0 0 */{value} * *"


def _bool(data: dict[str, Any], key: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int(data: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(data.get(key, default) or default)
    except (TypeError, ValueError) as exc:
        raise ValidationError({key: f"{key} must be an integer."}) from exc


def normalize_schedule(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError({"schedule": "schedule must be an object."})
    enabled = _bool(value, "enabled", True)
    raw_mode = str(value.get("mode") or "").strip().lower()
    if not raw_mode:
        cron_expr = validate_cron_expr(str(value.get("cron_expr") or ""))
        return {"enabled": enabled, "cron_expr": cron_expr}
    if raw_mode not in SCHEDULE_MODES:
        raise ValidationError({"schedule": "mode is invalid."})

    timezone_name = _normalize_schedule_timezone(value.get("timezone"))
    normalized: dict[str, Any] = {
        "enabled": enabled,
        "mode": raw_mode,
        "timezone": timezone_name,
        "starts_at": _normalize_schedule_starts_at(value.get("starts_at"), timezone_name),
    }
    if raw_mode == "advanced":
        normalized["cron_expr"] = validate_cron_expr(str(value.get("cron_expr") or ""))
        return normalized

    if raw_mode == "interval":
        unit = str(value.get("interval_unit") or "").strip().lower()
        if unit not in SCHEDULE_INTERVAL_LIMITS:
            raise ValidationError({"schedule": "interval_unit must be minute, hour, or day."})
        interval_value = _int(value, "interval_value")
        if interval_value < 1 or interval_value > SCHEDULE_INTERVAL_LIMITS[unit]:
            raise ValidationError(
                {"schedule": f"interval_value must be between 1 and {SCHEDULE_INTERVAL_LIMITS[unit]}."}
            )
        normalized.update(
            {
                "interval_unit": unit,
                "interval_value": interval_value,
                "cron_expr": _interval_cron(unit, interval_value),
            }
        )
        return normalized

    schedule_time = _normalize_schedule_time(value.get("time"))
    hour, minute = _schedule_time_parts(schedule_time)
    normalized["time"] = schedule_time
    if raw_mode == "daily":
        normalized["cron_expr"] = f"{minute} {hour} * * *"
        return normalized

    if raw_mode == "weekly":
        raw_weekdays = value.get("weekdays")
        if not isinstance(raw_weekdays, list):
            raise ValidationError({"schedule": "weekdays must be a non-empty list."})
        try:
            weekdays = sorted({int(day) for day in raw_weekdays})
        except (TypeError, ValueError) as exc:
            raise ValidationError({"schedule": "weekdays must contain integers from 1 to 7."}) from exc
        if not weekdays or any(day < 1 or day > 7 for day in weekdays):
            raise ValidationError({"schedule": "weekdays must contain integers from 1 to 7."})
        cron_weekdays = [0 if day == 7 else day for day in weekdays]
        normalized.update(
            {
                "weekdays": weekdays,
                "cron_expr": f"{minute} {hour} * * {','.join(str(day) for day in cron_weekdays)}",
            }
        )
        return normalized

    raw_month_days = value.get("month_days")
    if not isinstance(raw_month_days, list):
        raise ValidationError({"schedule": "month_days must be a list."})
    try:
        month_days = sorted({int(day) for day in raw_month_days})
    except (TypeError, ValueError) as exc:
        raise ValidationError({"schedule": "month_days must contain integers from 1 to 31."}) from exc
    month_end = _bool(value, "month_end", False)
    if any(day < 1 or day > 31 for day in month_days):
        raise ValidationError({"schedule": "month_days must contain integers from 1 to 31."})
    if not month_days and not month_end:
        raise ValidationError({"schedule": "select at least one month day or end of month."})
    # Keep cron_expr useful to legacy readers. Month-end adds day 31, which can
    # miss shorter months but cannot run on an extra day.
    cron_month_days = sorted({*month_days, *([31] if month_end else [])})
    normalized.update(
        {
            "month_days": month_days,
            "month_end": month_end,
            "cron_expr": f"{minute} {hour} {','.join(str(day) for day in cron_month_days)} * *",
        }
    )
    return normalized


def _coerce_retention_input(value: dict[str, Any]) -> dict[str, Any]:
    if "hourly_enabled" in value or "hourly_hours" in value or "hourly_days" in value:
        return value
    migrated = dict(value)
    if "short_hourly_enabled" in value:
        migrated.setdefault("hourly_enabled", value.get("short_hourly_enabled", True))
        short_days = _int(value, "short_days", 2)
        migrated.setdefault("hourly_hours", short_days * 24)
    if "mid_daily_enabled" in value:
        migrated.setdefault("daily_enabled", value.get("mid_daily_enabled", True))
        migrated.setdefault("daily_days", value.get("mid_days", 30))
    if "long_monthly_enabled" in value:
        migrated.setdefault("monthly_enabled", value.get("long_monthly_enabled", True))
        migrated.setdefault("monthly_months", value.get("long_months", 12))
    migrated.setdefault("weekly_enabled", False)
    migrated.setdefault("weekly_weeks", 4)
    migrated.setdefault("annual_enabled", False)
    migrated.setdefault("annual_years", 5)
    return migrated


def _resolve_hourly_hours(value: dict[str, Any]) -> int:
    if "hourly_hours" in value:
        return _int(value, "hourly_hours")
    if "hourly_days" in value:
        return _int(value, "hourly_days") * 24
    return 0


def normalize_retention(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError({"retention": "retention must be an object."})
    value = _coerce_retention_input(value)
    enabled = _bool(value, "enabled", True)
    recent_points = _int(value, "recent_points")
    hourly_enabled = _bool(value, "hourly_enabled", True)
    daily_enabled = _bool(value, "daily_enabled", True)
    weekly_enabled = _bool(value, "weekly_enabled", True)
    monthly_enabled = _bool(value, "monthly_enabled", True)
    annual_enabled = _bool(value, "annual_enabled", True)
    if recent_points < 1:
        raise ValidationError({"retention": "recent_points must be at least 1."})
    normalized = {
        "enabled": enabled,
        "recent_points": recent_points,
        "hourly_enabled": hourly_enabled,
        "daily_enabled": daily_enabled,
        "weekly_enabled": weekly_enabled,
        "monthly_enabled": monthly_enabled,
        "annual_enabled": annual_enabled,
    }
    tier_specs = (
        ("hourly", "hourly_hours", hourly_enabled, _resolve_hourly_hours, 87600),
        ("daily", "daily_days", daily_enabled, lambda raw: _int(raw, "daily_days"), 3650),
        ("weekly", "weekly_weeks", weekly_enabled, lambda raw: _int(raw, "weekly_weeks"), 520),
        ("monthly", "monthly_months", monthly_enabled, lambda raw: _int(raw, "monthly_months"), 120),
        ("annual", "annual_years", annual_enabled, lambda raw: _int(raw, "annual_years"), 100),
    )
    for label, field, tier_enabled, parse, maximum in tier_specs:
        if not tier_enabled:
            continue
        amount = parse(value)
        if amount < 1:
            raise ValidationError({"retention": f"{field} must be at least 1 when {label} retention is enabled."})
        if amount > maximum:
            raise ValidationError({"retention": f"{field} must be between 1 and {maximum}."})
        normalized[field] = amount
    return normalized


def normalize_throttling(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError({"throttling": "throttling must be an object."})
    enabled = _bool(value, "enabled", False)
    unlimited = _bool(value, "unlimited", True)
    rate_mbps = _int(value, "rate_mbps")
    if enabled and not unlimited and rate_mbps <= 0:
        raise ValidationError({"throttling": "rate_mbps must be greater than 0."})
    return {
        "enabled": enabled,
        "unlimited": unlimited,
        "rate_mbps": rate_mbps,
    }


def normalize_error_handling(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError({"error_handling": "error_handling must be an object."})
    return {
        "enabled": _bool(value, "enabled", False),
        "ignore_directory_read_errors": _bool(value, "ignore_directory_read_errors", True),
        "ignore_file_read_errors": _bool(value, "ignore_file_read_errors", False),
        "ignore_unknown_entries": _bool(value, "ignore_unknown_entries", True),
    }


def _humanize_cron_expression(expr: str) -> str:
    cron_expr = str(expr or "").strip()
    if not cron_expr:
        return "Not set"
    parts = cron_expr.split()
    if len(parts) != 5:
        return "Custom schedule"

    minute, hour, dom, month, dow = parts

    def is_star(field: str) -> bool:
        return field == "*"

    minute_step = re.fullmatch(r"\*/(\d+)", minute)
    if minute_step and is_star(hour) and is_star(dom) and is_star(month) and is_star(dow):
        n = int(minute_step.group(1))
        suffix = "" if n == 1 else "s"
        return f"Every {n} minute{suffix}"

    hour_step = re.fullmatch(r"\*/(\d+)", hour)
    if minute in {"0", "00"} and hour_step and is_star(dom) and is_star(month) and is_star(dow):
        n = int(hour_step.group(1))
        suffix = "" if n == 1 else "s"
        return f"Every {n} hour{suffix}"

    dom_step = re.fullmatch(r"\*/(\d+)", dom)
    if minute in {"0", "00"} and hour in {"0", "00"} and dom_step and is_star(month) and is_star(dow):
        n = int(dom_step.group(1))
        suffix = "" if n == 1 else "s"
        return f"Every {n} day{suffix}"

    if minute.isdigit() and hour.isdigit() and is_star(dom) and is_star(month) and is_star(dow):
        return f"Daily at {int(hour):02d}:{int(minute):02d}"

    if minute.isdigit() and is_star(hour) and is_star(dom) and is_star(month) and is_star(dow):
        return f"At minute {int(minute)} of every hour"

    if minute in {"0", "00"} and hour.isdigit() and is_star(dom) and is_star(month) and dow.isdigit():
        weekday = int(dow)
        if 0 <= weekday <= 6:
            weekday_names = [
                "Sunday",
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
            ]
            return f"Weekly on {weekday_names[weekday]} at {int(hour):02d}:00"

    if minute in {"0", "00"} and hour.isdigit() and dom.isdigit() and is_star(month) and is_star(dow):
        return f"Monthly on day {int(dom)} at {int(hour):02d}:00"

    return "Custom schedule"


def _structured_schedule_summary(schedule: dict[str, Any]) -> str:
    mode = str(schedule.get("mode") or "")
    timezone_name = str(schedule.get("timezone") or "UTC")
    starts_at = str(schedule.get("starts_at") or "")
    if mode == "interval":
        value = int(schedule.get("interval_value") or 1)
        unit = str(schedule.get("interval_unit") or "minute")
        summary = f"Every {value} {unit}{'' if value == 1 else 's'}"
    elif mode == "daily":
        summary = f"Daily at {schedule.get('time')}"
    elif mode == "weekly":
        weekday_names = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        labels = [weekday_names[int(day) - 1] for day in schedule.get("weekdays", [])]
        summary = f"Weekly on {', '.join(labels)} at {schedule.get('time')}"
    elif mode == "monthly":
        labels = [str(day) for day in schedule.get("month_days", [])]
        if schedule.get("month_end"):
            labels.append("end of month")
        summary = f"Monthly on {', '.join(labels)} at {schedule.get('time')}"
    else:
        summary = _humanize_cron_expression(str(schedule.get("cron_expr") or ""))
    summary = f"{summary} ({timezone_name})"
    if starts_at:
        summary = f"{summary}, starts {starts_at}"
    return summary


def backup_policy_schedule_summary(policy: BackupPolicy) -> str:
    schedule = policy.schedule or {}
    if not schedule.get("enabled", False):
        return "Not configured"
    if schedule.get("mode") in SCHEDULE_MODES:
        return _structured_schedule_summary(schedule)
    return _humanize_cron_expression(str(schedule.get("cron_expr") or ""))


def _retention_hourly_hours(retention: dict[str, Any]) -> int:
    if retention.get("hourly_hours") is not None:
        return int(retention.get("hourly_hours") or 0)
    if retention.get("hourly_days") is not None:
        return int(retention.get("hourly_days") or 0) * 24
    return 0


def backup_policy_retention_summary(policy: BackupPolicy) -> str:
    retention = policy.retention or {}
    if not retention.get("enabled", False):
        return "Not configured"
    parts = [f"Latest {retention.get('recent_points', 0)}"]
    if retention.get("hourly_enabled"):
        parts.append(f"H {_retention_hourly_hours(retention)}h")
    if retention.get("daily_enabled"):
        parts.append(f"D {retention.get('daily_days', 0)}d")
    if retention.get("weekly_enabled"):
        parts.append(f"W {retention.get('weekly_weeks', 0)}w")
    if retention.get("monthly_enabled"):
        parts.append(f"M {retention.get('monthly_months', 0)}mo")
    if retention.get("annual_enabled"):
        parts.append(f"Y {retention.get('annual_years', 0)}y")
    return " · ".join(parts)


def _summarize_ignore_patterns(patterns_text: str, *, limit: int | None = None) -> str:
    lines = [
        line.strip()
        for line in str(patterns_text or "").splitlines()
        if line.strip() and not line.strip().startswith("!")
    ]
    if not lines:
        return ""
    exts: list[str] = []
    paths: list[str] = []
    for line in lines:
        if "*." in line:
            exts.append(line[line.index("*.") :])
        else:
            paths.append(line.replace("**/", "").replace("/**", "/"))
    bits: list[str] = []
    if exts:
        ext_text = ", ".join(exts if limit is None else exts[:limit])
        if limit is not None and len(exts) > limit:
            ext_text = f"{ext_text} +{len(exts) - limit}"
        bits.append(ext_text)
    if paths:
        path_limit = limit if limit is not None else len(paths)
        path_text = ", ".join(paths if limit is None else paths[:path_limit])
        if limit is not None and len(paths) > path_limit:
            path_text = f"{path_text} +{len(paths) - path_limit}"
        bits.append(path_text)
    return " · ".join(bits)


def _summarize_exception_patterns(patterns_text: str, *, limit: int | None = None) -> str:
    lines = [
        line.strip()[1:].strip()
        for line in str(patterns_text or "").splitlines()
        if line.strip().startswith("!") and line.strip()[1:].strip()
    ]
    if not lines:
        return ""
    text = ", ".join(lines if limit is None else lines[:limit])
    if limit is not None and len(lines) > limit:
        text = f"{text} +{len(lines) - limit}"
    return text


def _format_large_file_limit(bytes_max: int) -> str:
    if bytes_max >= 1024 * 1024 * 1024 and bytes_max % (1024 * 1024 * 1024) == 0:
        return f">{bytes_max // (1024 * 1024 * 1024)} GB"
    if bytes_max >= 1024 * 1024 and bytes_max % (1024 * 1024) == 0:
        return f">{bytes_max // (1024 * 1024)} MB"
    if bytes_max >= 1024 and bytes_max % 1024 == 0:
        return f">{bytes_max // 1024} KB"
    return f">{bytes_max} B"


def _file_filter_advanced_summary(rule: FileFilterRule) -> str:
    parts: list[str] = []
    if rule.large_file_limit_enabled and rule.large_file_bytes_max > 0:
        parts.append(_format_large_file_limit(int(rule.large_file_bytes_max)))
    else:
        parts.append("No size limit")
    parts.append("Skip cache dirs" if rule.ignore_cache_directories else "Include cache dirs")
    if rule.current_filesystem_only:
        parts.append("Current FS only")
    return " · ".join(parts)


def file_filter_summary(rule: FileFilterRule) -> str:
    pattern_part = _summarize_ignore_patterns(rule.ignore_patterns)
    exception_part = _summarize_exception_patterns(rule.ignore_patterns)
    exclude_value = pattern_part or "None"
    advanced_value = _file_filter_advanced_summary(rule)
    parts = [f"Exclude: {exclude_value}"]
    if exception_part:
        parts.append(f"Exceptions: {exception_part}")
    parts.append(f"Advanced: {advanced_value}")
    return "\n".join(parts)


def _policy_payload(data: dict[str, Any], current: BackupPolicy | None = None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if current is not None:
        merged = {
            "name": current.name,
            "is_active": current.is_active,
            "schedule": current.schedule,
            "retention": current.retention,
            "throttling": current.throttling,
            "error_handling": current.error_handling,
        }
    merged.update(data)
    name = str(merged.get("name") or "").strip()
    if not name:
        raise ValidationError({"name": "name is required."})
    return {
        "name": name,
        "is_active": bool(merged.get("is_active", True)),
        "schedule": normalize_schedule(merged.get("schedule")),
        "retention": normalize_retention(merged.get("retention")),
        "throttling": normalize_throttling(merged.get("throttling")),
        "error_handling": normalize_error_handling(merged.get("error_handling")),
    }


def create_backup_policy(*, organization_id: int, data: dict[str, Any]) -> BackupPolicy:
    payload = _policy_payload(data)
    try:
        with transaction.atomic():
            return BackupPolicy.objects.create(organization_id=organization_id, **payload)
    except IntegrityError as exc:
        raise ValidationError({"name": "A backup policy with this name already exists."}) from exc


def update_backup_policy(*, policy: BackupPolicy, data: dict[str, Any]) -> BackupPolicy:
    payload = _policy_payload(data, current=policy)
    for field, value in payload.items():
        setattr(policy, field, value)
    try:
        with transaction.atomic():
            policy.save()
    except IntegrityError as exc:
        raise ValidationError({"name": "A backup policy with this name already exists."}) from exc
    return policy


def backup_policy_related_count(*, policy: BackupPolicy) -> int:
    return BackupConfig.objects.filter(
        organization_id=policy.organization_id,
        backup_policy_id=policy.id,
    ).count()


def ensure_backup_policy_not_referenced(*, policy: BackupPolicy) -> None:
    if backup_policy_related_count(policy=policy) > 0:
        raise ResourceInUseError("Backup policy is referenced by backup configs.")


def delete_backup_policy(*, policy: BackupPolicy) -> dict[str, Any]:
    policy_id = int(policy.id)
    ensure_backup_policy_not_referenced(policy=policy)
    policy.delete()
    return {"deleted": True, "id": policy_id}


def bulk_set_backup_policy_state(
    *,
    organization_id: int,
    ids: list[int],
    is_active: bool,
) -> dict[str, Any]:
    if not ids:
        raise ValidationError({"ids": "ids must not be empty."})
    existing = list(
        BackupPolicy.objects.filter(organization_id=organization_id, id__in=ids)
    )
    by_id = {int(policy.id): policy for policy in existing}
    updated: list[int] = []
    failed = [
        BulkFailure(id=policy_id, reason="not_found")
        for policy_id in ids
        if policy_id not in by_id
    ]
    with transaction.atomic():
        for policy in existing:
            if policy.is_active != is_active:
                policy.is_active = is_active
                policy.save(update_fields=["is_active", "updated_at"])
            updated.append(int(policy.id))
    return {"updated": updated, "failed": [failure.__dict__ for failure in failed]}


def bulk_delete_backup_policies(*, organization_id: int, ids: list[int]) -> dict[str, Any]:
    if not ids:
        raise ValidationError({"ids": "ids must not be empty."})
    existing = list(
        BackupPolicy.objects.filter(organization_id=organization_id, id__in=ids)
    )
    by_id = {int(policy.id): policy for policy in existing}
    deleted: list[int] = []
    failed = [
        BulkFailure(id=policy_id, reason="not_found")
        for policy_id in ids
        if policy_id not in by_id
    ]
    for policy in existing:
        try:
            result = delete_backup_policy(policy=policy)
            deleted.append(int(result["id"]))
        except ResourceInUseError as exc:
            failed.append(BulkFailure(id=int(policy.id), reason=str(exc)))
    return {"deleted": deleted, "failed": [failure.__dict__ for failure in failed]}


def _file_filter_payload(
    data: dict[str, Any],
    current: FileFilterRule | None = None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if current is not None:
        merged = {
            "name": current.name,
            "is_active": current.is_active,
            "ignore_patterns": current.ignore_patterns,
            "large_file_limit_enabled": current.large_file_limit_enabled,
            "large_file_bytes_max": current.large_file_bytes_max,
            "ignore_cache_directories": current.ignore_cache_directories,
            "current_filesystem_only": current.current_filesystem_only,
        }
    merged.update(data)
    name = str(merged.get("name") or "").strip()
    if not name:
        raise ValidationError({"name": "name is required."})
    large_file_limit_enabled = bool(merged.get("large_file_limit_enabled", False))
    large_file_bytes_max = _int(merged, "large_file_bytes_max")
    if large_file_limit_enabled and large_file_bytes_max <= 0:
        raise ValidationError(
            {"large_file_bytes_max": "large_file_bytes_max must be greater than 0."}
        )
    if not large_file_limit_enabled:
        large_file_bytes_max = 0
    return {
        "name": name,
        "is_active": bool(merged.get("is_active", True)),
        "ignore_patterns": normalize_ignore_patterns(merged.get("ignore_patterns")),
        "large_file_limit_enabled": large_file_limit_enabled,
        "large_file_bytes_max": large_file_bytes_max,
        "ignore_cache_directories": bool(merged.get("ignore_cache_directories", True)),
        "current_filesystem_only": bool(merged.get("current_filesystem_only", False)),
    }


def create_file_filter_rule(*, organization_id: int, data: dict[str, Any]) -> FileFilterRule:
    payload = _file_filter_payload(data)
    try:
        with transaction.atomic():
            return FileFilterRule.objects.create(organization_id=organization_id, **payload)
    except IntegrityError as exc:
        raise ValidationError(
            {"name": "A file filter rule with this name already exists."}
        ) from exc


def update_file_filter_rule(
    *,
    rule: FileFilterRule,
    data: dict[str, Any],
) -> FileFilterRule:
    payload = _file_filter_payload(data, current=rule)
    for field, value in payload.items():
        setattr(rule, field, value)
    try:
        with transaction.atomic():
            rule.save()
    except IntegrityError as exc:
        raise ValidationError(
            {"name": "A file filter rule with this name already exists."}
        ) from exc
    return rule


def file_filter_related_count(*, rule: FileFilterRule) -> int:
    return BackupConfig.objects.filter(
        organization_id=rule.organization_id,
        file_filter_rule_id=rule.id,
    ).count()


def ensure_file_filter_not_referenced(*, rule: FileFilterRule) -> None:
    if file_filter_related_count(rule=rule) > 0:
        raise ResourceInUseError("File filter rule is referenced by backup configs.")


def delete_file_filter_rule(*, rule: FileFilterRule) -> dict[str, Any]:
    rule_id = int(rule.id)
    ensure_file_filter_not_referenced(rule=rule)
    rule.delete()
    return {"deleted": True, "id": rule_id}


def bulk_set_file_filter_state(
    *,
    organization_id: int,
    ids: list[int],
    is_active: bool,
) -> dict[str, Any]:
    if not ids:
        raise ValidationError({"ids": "ids must not be empty."})
    existing = list(
        FileFilterRule.objects.filter(organization_id=organization_id, id__in=ids)
    )
    by_id = {int(rule.id): rule for rule in existing}
    updated: list[int] = []
    failed = [
        BulkFailure(id=rule_id, reason="not_found")
        for rule_id in ids
        if rule_id not in by_id
    ]
    with transaction.atomic():
        for rule in existing:
            if rule.is_active != is_active:
                rule.is_active = is_active
                rule.save(update_fields=["is_active", "updated_at"])
            updated.append(int(rule.id))
    return {"updated": updated, "failed": [failure.__dict__ for failure in failed]}


def bulk_delete_file_filters(*, organization_id: int, ids: list[int]) -> dict[str, Any]:
    if not ids:
        raise ValidationError({"ids": "ids must not be empty."})
    existing = list(
        FileFilterRule.objects.filter(organization_id=organization_id, id__in=ids)
    )
    by_id = {int(rule.id): rule for rule in existing}
    deleted: list[int] = []
    failed = [
        BulkFailure(id=rule_id, reason="not_found")
        for rule_id in ids
        if rule_id not in by_id
    ]
    for rule in existing:
        try:
            result = delete_file_filter_rule(rule=rule)
            deleted.append(int(result["id"]))
        except ResourceInUseError as exc:
            failed.append(BulkFailure(id=int(rule.id), reason=str(exc)))
    return {"deleted": deleted, "failed": [failure.__dict__ for failure in failed]}
