"""Cluster-safe Celery Beat scheduler with coalesced periodic wake-ups."""

from __future__ import annotations

from copy import copy

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

        claimed_entry = copy(entry)
        claimed_entry.options = dict(entry.options)
        stamped_headers = list(claimed_entry.options.get("stamped_headers") or [])
        for header in (
            PERIODIC_WAKEUP_NAME_HEADER,
            PERIODIC_WAKEUP_TOKEN_HEADER,
        ):
            if header not in stamped_headers:
                stamped_headers.append(header)
        claimed_entry.options["stamped_headers"] = stamped_headers
        claimed_entry.options[PERIODIC_WAKEUP_NAME_HEADER] = entry.name
        claimed_entry.options[PERIODIC_WAKEUP_TOKEN_HEADER] = claim
        if advance:
            self.reserve(entry)
        try:
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
