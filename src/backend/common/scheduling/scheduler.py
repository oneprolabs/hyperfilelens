"""Cluster-safe Celery Beat scheduler with coalesced periodic wake-ups."""

from __future__ import annotations

from celery.beat import ScheduleEntry
from django_celery_beat.schedulers import DatabaseScheduler

from common.scheduling.periodic_wakeup import (
    PERIODIC_WAKEUP_COALESCE_HEADER,
    PERIODIC_WAKEUP_NAME_HEADER,
    PERIODIC_WAKEUP_TOKEN_HEADER,
    claim_periodic_wakeup,
    promote_periodic_wakeup,
    release_periodic_wakeup,
)


class CoalescingDatabaseScheduler(DatabaseScheduler):
    """Keep one queued or running wake-up for explicitly managed schedules."""

    @staticmethod
    def _coalescing_enabled(entry) -> bool:
        headers = entry.options.get("headers") or {}
        return headers.get(PERIODIC_WAKEUP_COALESCE_HEADER) is True

    def _record_dispatch_attempt(self, entry, *, advance: bool) -> None:
        # ``tick`` reserves before calling ``apply_entry`` (advance=False),
        # while direct callers rely on ``apply_async`` to reserve.
        if advance:
            self.reserve(entry)
        self._tasks_since_sync += 1
        if self.should_sync():
            self._do_sync()

    @staticmethod
    def _publishing_entry(entry, *, options: dict) -> ScheduleEntry:
        """Build a detached Celery entry without copying a Django ModelEntry."""
        return ScheduleEntry(
            name=entry.name,
            task=entry.task,
            last_run_at=entry.last_run_at,
            total_run_count=entry.total_run_count,
            schedule=entry.schedule,
            args=entry.args,
            kwargs=entry.kwargs,
            options=options,
            app=entry.app,
        )

    def apply_async(self, entry, producer=None, advance=True, **kwargs):
        if not self._coalescing_enabled(entry):
            return super().apply_async(
                entry,
                producer=producer,
                advance=advance,
                **kwargs,
            )
        claim = claim_periodic_wakeup(entry.name)
        if claim is False:
            self._record_dispatch_attempt(entry, advance=advance)
            return None
        if claim is None:
            return super().apply_async(
                entry,
                producer=producer,
                advance=advance,
                **kwargs,
            )

        try:
            options = dict(entry.options)
            stamped_headers = list(options.get("stamped_headers") or [])
            for header in (
                PERIODIC_WAKEUP_NAME_HEADER,
                PERIODIC_WAKEUP_TOKEN_HEADER,
            ):
                if header not in stamped_headers:
                    stamped_headers.append(header)
            options["stamped_headers"] = stamped_headers
            options[PERIODIC_WAKEUP_NAME_HEADER] = entry.name
            options[PERIODIC_WAKEUP_TOKEN_HEADER] = claim
            claimed_entry = self._publishing_entry(entry, options=options)
            if advance:
                self.reserve(entry)
            result = super().apply_async(
                claimed_entry,
                producer=producer,
                # Reserve the original database-backed entry, not the shallow
                # copy carrying transient broker headers.
                advance=False,
                **kwargs,
            )
            promote_periodic_wakeup(entry.name, claim)
            return result
        except Exception:
            release_periodic_wakeup(entry.name, claim)
            raise
