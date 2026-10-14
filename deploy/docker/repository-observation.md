# Repository observation budgets and cadence

Automatic usage and health observations default to 1,800 seconds:

- `STORAGE_REPOSITORY_USAGE_INTERVAL_SECONDS`
- `STORAGE_REPOSITORY_HEALTH_INTERVAL_SECONDS`

Both accept intervals of at least 60 seconds. Managed schedules run cheap
one-minute due checks; each repository has a stable offset in its fixed cycle.
The default offset leaves three minutes before cycle end for execution. Failure
or insufficient capacity can still make an observation late. Do not treat an
empty Celery queue as proof of fresh measurements.

Legacy managed 15-minute usage and 5-minute health schedules migrate to due
checks. Explicitly customized database schedules are retained; interval
customizations retain their observation cadence and do not use staggering.
Custom cron schedules continue to determine collection times. Deployment
environment overrides determine cadence for the normal managed due checks.

## Independent admission pools

| Setting | Default | Scope |
| --- | --- | --- |
| `CELERY_STORAGE_HEALTH_CONCURRENCY` | 1 | Controller-local health probes |
| `CELERY_STORAGE_USAGE_CONCURRENCY` | 1 | Background usage collection |
| `CELERY_STORAGE_MAINTENANCE_CONCURRENCY` | derived, at least 1 | Existing maintenance |

`CELERY_BACKGROUND_STORAGE_CONCURRENCY` remains a usage-budget fallback when
the new usage setting is absent. It no longer limits health. Quick and full
maintenance continue to share the maintenance budget and repository locks.

These are Redis admission limits, not reserved Worker processes. The existing
prefork Worker still consumes all configured queues. Separating pools increases
the possible simultaneous storage work compared with the old shared pool:
review aggregate memory, CPU, database connections, cache disk and Kopia
processes before rollout. The two-process small-deployment default remains
supported but cannot guarantee spare business slots. Four processes with one
allowance per class are a reasonable evaluation configuration, not an automatic
production change or a performance guarantee.

Capacity deferral is not a health failure. Short bounded retries use jitter;
after exhaustion the fixed-cycle due checks may try again, with at most three
root dispatches per repository per cycle. Actual usage collection failure keeps
the cycle fence until the next cycle; it must not restart every two minutes.
The retry counter survives the shorter admission cooldown. Config/file locks
and maintenance ownership still take precedence over free admission slots.

## Read-only collection reuse

Usage reuses its stable read-only Kopia config only when its connection
fingerprint matches. Target/credential changes invalidate the connection.
Collection keeps a separate client from maintenance, with an exclusive
collection fence. Do not delete live caches to benchmark cold-cache behavior.
CLI JSON support is detected once per installed binary revision.

Compose mounts `data/kopia-usage-cache` at `/var/cache/hyperfilelens/usage`.
This persists observation indexes across container recreation, not connection
configs or credentials. `HFL_KOPIA_USAGE_CACHE_DIR` can override the cache root.
Non-Compose environments fall back to the client's cache directory.
Failed connect verification uses disposable staging so Kopia's failure cleanup
cannot erase the persistent warm cache. Published usage configs omit S3
credential fields; each CLI invocation receives them through its environment.

Periodic, manual and completion-triggered requests coalesce per repository.
Manual refresh remains asynchronous. Completion-triggered requests are spaced
by at least two minutes relative to the last successful observation and retain
one coalesced follow-up during active collection.

## History

All repository monitoring ranges use 30-minute buckets. New rows retain the
real successful observation time in `observed_at`; old rows have unknown exact
observation time and retain their historical slot timestamp as the compatibility
fallback. Delayed collection is recorded when observed, not at its original
request time. Successful zero values are distinct from missing observations.
Successful observations in an active bucket are shown immediately, including
successful zero values. An empty active bucket is withheld until it closes and
a five-minute grace expires, so staggered probes do not create premature gaps.
Failures retain the last
successful repository value but never fabricate fresh chart samples.

Longer explicit observation intervals can legitimately leave missing chart
buckets. Thirty-minute estimates are not a new real-time hard-quota signal.
Health success does not refresh usage freshness. No S3 Proxy relocation or
maintenance policy change is included.
