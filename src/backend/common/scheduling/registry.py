"""
Registry for periodic tasks.

Apps register entries via ``register_periodic_tasks()`` and this registry
writes them into ``django_celery_beat``. Existing rows are left untouched so
database-side customisations are preserved, except for explicitly managed
delivery-safety fields such as ``expire_seconds``.
"""

import json
import logging

from common.scheduling.periodic_wakeup import PERIODIC_WAKEUP_COALESCE_HEADER


logger = logging.getLogger(__name__)


def _is_crontab_schedule(schedule) -> bool:
    return hasattr(schedule, "_orig_minute")


def _get_or_create_crontab(schedule):
    from django.conf import settings
    from django_celery_beat.models import CrontabSchedule

    try:
        obj, created = CrontabSchedule.from_schedule(schedule)
        if created:
            obj.save()
        return obj
    except (AttributeError, TypeError):
        tz = getattr(schedule, "tz", None) or getattr(
            settings, "CELERY_TIMEZONE", None
        )
        spec = {
            "minute": getattr(schedule, "_orig_minute", "*"),
            "hour": getattr(schedule, "_orig_hour", "*"),
            "day_of_week": getattr(schedule, "_orig_day_of_week", "*"),
            "day_of_month": getattr(schedule, "_orig_day_of_month", "*"),
            "month_of_year": getattr(schedule, "_orig_month_of_year", "*"),
        }
        if tz:
            spec["timezone"] = tz
        obj, _ = CrontabSchedule.objects.get_or_create(**spec)
        return obj


def _get_or_create_interval_seconds(seconds: int):
    from django_celery_beat.models import IntervalSchedule

    every = max(int(seconds), 1)
    obj, _ = IntervalSchedule.objects.get_or_create(
        every=every,
        period=IntervalSchedule.SECONDS,
    )
    return obj


class TaskRegistry:
    """
    In-memory registry of periodic task definitions.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def add(
        self,
        name: str,
        task: str,
        schedule,
        args=(),
        kwargs: dict | None = None,
        queue: str | None = None,
        enabled: bool = True,
        expire_seconds: int | None = None,
        coalesce_wakeup: bool = True,
        sync_existing_kwargs: bool = False,
    ) -> None:
        if expire_seconds is not None and int(expire_seconds) < 1:
            raise ValueError("expire_seconds must be positive when configured")
        self._entries[name] = {
            "task": task,
            "schedule": schedule,
            "args": tuple(args) if args else (),
            "kwargs": dict(kwargs) if kwargs else {},
            "queue": queue,
            "enabled": enabled,
            "expire_seconds": (
                int(expire_seconds) if expire_seconds is not None else None
            ),
            "coalesce_wakeup": bool(coalesce_wakeup),
            "sync_existing_kwargs": bool(sync_existing_kwargs),
        }

    def _apply_one(self, name: str, entry: dict) -> bool:
        from django_celery_beat.models import PeriodicTask, PeriodicTasks

        schedule = entry["schedule"]
        if _is_crontab_schedule(schedule):
            crontab_schedule = _get_or_create_crontab(schedule)
            interval_schedule = None
        elif isinstance(schedule, (int, float)):
            interval_schedule = _get_or_create_interval_seconds(schedule)
            crontab_schedule = None
        else:
            run_every = getattr(schedule, "run_every", None)
            if run_every is not None:
                secs = run_every.total_seconds()
                interval_schedule = _get_or_create_interval_seconds(secs)
                crontab_schedule = None
            else:
                crontab_schedule = _get_or_create_crontab(schedule)
                interval_schedule = None

        defaults = {
            "task": entry["task"],
            "args": json.dumps(list(entry["args"])),
            "kwargs": json.dumps(entry["kwargs"]),
            "queue": entry["queue"],
            "enabled": entry["enabled"],
            "headers": json.dumps(
                {PERIODIC_WAKEUP_COALESCE_HEADER: entry["coalesce_wakeup"]}
            ),
        }
        if entry["expire_seconds"] is not None:
            defaults["expire_seconds"] = entry["expire_seconds"]
        if crontab_schedule is not None:
            defaults["crontab"] = crontab_schedule
            defaults["interval"] = None
        else:
            defaults["interval"] = interval_schedule
            defaults["crontab"] = None

        obj, created = PeriodicTask.objects.get_or_create(
            name=name, defaults=defaults
        )
        if not created:
            headers = json.loads(obj.headers or "{}")
            expected_coalescing = entry["coalesce_wakeup"]
            headers_changed = (
                headers.get(PERIODIC_WAKEUP_COALESCE_HEADER)
                is not expected_coalescing
            )
            kwargs_changed = (
                entry["sync_existing_kwargs"]
                and obj.kwargs != defaults["kwargs"]
            )
            if kwargs_changed:
                obj.kwargs = defaults["kwargs"]
            if headers_changed:
                headers[PERIODIC_WAKEUP_COALESCE_HEADER] = expected_coalescing
                obj.headers = json.dumps(headers)
            expire_seconds = entry["expire_seconds"]
            expiry_changed = (
                expire_seconds is not None
                and obj.expire_seconds != expire_seconds
            )
            if not headers_changed and not expiry_changed and not kwargs_changed:
                logger.debug("Periodic task exists, skipping update: %s", name)
                return False
            update_fields = ["headers"] if headers_changed else []
            if kwargs_changed:
                update_fields.append("kwargs")
            if expiry_changed:
                obj.expire_seconds = expire_seconds
                update_fields.append("expire_seconds")
            obj.save(update_fields=update_fields)
            logger.info(
                "Updated periodic task delivery contract: %s "
                "expire_seconds=%s coalesce_wakeup=%s",
                name,
                expire_seconds,
                expected_coalescing,
            )

        PeriodicTasks.update_changed()
        return True

    def apply(self) -> None:
        for name, entry in self._entries.items():
            try:
                created = self._apply_one(name, entry)
                if created:
                    logger.debug("Registered periodic task: %s", name)
            except Exception as exc:
                logger.exception(
                    "Failed to register periodic task %s: %s", name, exc
                )


TASK_REGISTRY = TaskRegistry()


def apply_registry() -> None:
    TASK_REGISTRY.apply()


def remove_periodic_tasks(*, names=(), task_names=()) -> int:
    from django_celery_beat.models import PeriodicTask, PeriodicTasks

    name_set = {str(name) for name in names if name}
    task_name_set = {str(task_name) for task_name in task_names if task_name}
    if not name_set and not task_name_set:
        return 0

    queryset = PeriodicTask.objects.none()
    if name_set:
        queryset = queryset | PeriodicTask.objects.filter(name__in=name_set)
    if task_name_set:
        queryset = queryset | PeriodicTask.objects.filter(task__in=task_name_set)

    deleted_count, _ = queryset.delete()
    if deleted_count:
        PeriodicTasks.update_changed()
    return deleted_count
