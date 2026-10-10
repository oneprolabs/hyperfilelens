"""Stable local-calendar schedules, independent of maintenance duration."""

import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from django.core.exceptions import ImproperlyConfigured


ANCHOR = date(2026, 1, 1)


def server_timezone() -> str:
    explicit = os.getenv("STORAGE_MAINTENANCE_TIMEZONE") or os.getenv("TZ")
    if explicit:
        return explicit.strip().removeprefix(":")
    timezone_file = Path("/etc/timezone")
    if timezone_file.exists():
        value = timezone_file.read_text().strip()
        if value:
            return value
    resolved = str(Path("/etc/localtime").resolve())
    if "/zoneinfo/" in resolved:
        return resolved.split("/zoneinfo/", 1)[1]
    raise ImproperlyConfigured(
        "Configure STORAGE_MAINTENANCE_TIMEZONE with the Controller server IANA timezone."
    )


def slot_on(day: date, seconds: int, zone: ZoneInfo, end: time) -> datetime | None:
    """Resolve gaps by moving forward; choose the first fold exactly once."""
    naive = datetime.combine(day, time()) + timedelta(seconds=seconds)
    while naive.date() == day and naive.time() < end:
        local = naive.replace(tzinfo=zone, fold=0)
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            return utc
        naive += timedelta(minutes=1)
    return None


def next_full_slot(state, now, settings, *, include_now=False):
    zone = settings.timezone
    day = now.astimezone(zone).date()
    cycle_days = max(2, round(settings.full_interval.total_seconds() / 86400))
    for offset in range(cycle_days * 2 + 1):
        candidate_day = day + timedelta(days=offset)
        if (candidate_day - ANCHOR).days % cycle_days != state.full_day_group:
            continue
        instant = slot_on(candidate_day, state.full_slot_seconds, zone, settings.window_end)
        if instant is not None and (instant > now or (include_now and instant == now)):
            return instant
    raise ImproperlyConfigured("Maintenance window has no valid local scheduling slot.")


def assign_full_slots(targets, settings, now):
    """Persist balanced assignments; additions never reshuffle existing slots."""
    zone_name = str(settings.timezone)
    cycle_days = max(2, round(settings.full_interval.total_seconds() / 86400))
    start = settings.window_start.hour * 3600 + settings.window_start.minute * 60
    end = settings.window_end.hour * 3600 + settings.window_end.minute * 60
    groups = [[] for _ in range(cycle_days)]
    unassigned = []
    for target in targets:
        state = target.maintenance_state
        if (
            state.full_day_group is None or state.full_day_group >= cycle_days
            or state.full_slot_seconds is None
            or not start <= state.full_slot_seconds < end
        ):
            unassigned.append(state)
        else:
            groups[state.full_day_group].append(state.full_slot_seconds)
            if state.schedule_timezone != zone_name:
                state.schedule_timezone = zone_name
                state.next_full_due_at = next_full_slot(state, now, settings)
                state.save(update_fields=["schedule_timezone", "next_full_due_at"])
    for state in unassigned:
        group = min(range(cycle_days), key=lambda i: len(groups[i]))
        # Split the largest available gap, retaining all existing reservations.
        boundaries = [start, *sorted(groups[group]), end]
        left, right = max(zip(boundaries, boundaries[1:]), key=lambda pair: pair[1] - pair[0])
        seconds = (left + right) // 2
        groups[group].append(seconds)
        state.full_day_group = group
        state.full_slot_seconds = seconds
        state.schedule_timezone = zone_name
        state.next_full_due_at = next_full_slot(state, now, settings)
        state.next_reconcile_due_at = state.next_full_due_at + timedelta(hours=6)
        state.save()
