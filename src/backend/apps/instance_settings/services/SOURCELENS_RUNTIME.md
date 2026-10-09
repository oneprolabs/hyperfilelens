# SourceLens Runtime Environment alerts (HFL only)

This is a small addition to the existing Runtime Environment table, not a
container monitor or an SL patch. Runtime State remains Not Monitored. API
readiness remains independent of database log evidence and queue warnings.
There are no Chat/task IDs, task payloads, Docker socket access, repairs or queue
deletions.

## Index error evidence

Bundled deployments already mount `data/sourcelens/logs` read-only at
`/var/log/sourcelens` in both development and production HFL Compose files.
HFL incrementally scans PostgreSQL and Worker logs for `unexpected zero page` and
`right sibling's left-link doesn't match`.

- Maximum four files per directory, a 256 KiB data window per file per scan
  (plus one boundary-check byte on jumps), 512 directory entries.
- Regular files only; no symlink files. The scan is best-effort, not a complete
  historical search or an index integrity check.
- PostgreSQL/Celery event timestamps are used, not file modification times.
  Naive timestamps are interpreted as UTC, matching the packaged SL runtime.
- First checks prioritize the most recent 256 KiB. Cached inode/offset checkpoints
  resume normal append-only reading; if new data exceeds the budget, the check
  jumps to the latest window instead of waiting behind old log traffic.
  Truncation/replacement likewise checks the latest bounded window. This is a
  recent-log probe, **not historical backfill**: records outside the window may
  be missed, and absence of errors never proves database integrity.
- A jump checks the preceding byte: a complete first record is kept when the
  window already starts on a line boundary. Only genuinely partial first records
  are discarded, not interpreted as new error messages. Error context is preserved for normal
  incremental reads; oversized lines are skipped rather than parsed as fragments.
  File size alone does not produce a scan-pending/degraded status.
- Match only explicit ERROR/FATAL/PANIC messages or database exception records.
  Ordinary LOG/INFO, SQL statements and source-code stack frames are excluded.
- Errors within the last 24 hours produce a red Error notice and Unhealthy
  Health Check on PostgreSQL. Business Availability remains unknown: an index
  error does not prove that every database query fails.
- First-discovered older/undated evidence produces an informational notice
  explicitly saying current integrity is not confirmed. Previously detected
  recent errors remain in the HFL checkpoint even when logs grow/rotate/disappear.
  After 24 hours their red notice becomes a yellow "recovery not confirmed"
  warning; age/normal activity never automatically declares recovery.
- Checkpoints/evidence are in the existing HFL cache, refreshed with a seven-day
  TTL. They are not durable database records: cache eviction/expiry loses them
  and restarts scanning. Cache failure is an incomplete-monitoring Warning.
  If a checkpoint read fails, the probe may report current log evidence, but
  does not write any checkpoint that round; retained cache data stays untouched,
  and its owned lease is still released safely.
  Confirm repairs independently; this probe cannot validate index integrity
  or automatically acknowledge/clear an incident.
- A non-blocking, per-log-root cache lease serializes scans/checkpoint writes.
  Contending requests reuse retained evidence with the existing scan-incomplete
  warning; they neither scan nor overwrite checkpoints. Checkpoint reads also
  use the HFL cache primary, not a potentially lagging read replica. Redis validates ownership
  and writes the checkpoint in one Lua operation on the **HFL cache primary**,
  preserving Django's key namespace and serializer. Conditional release also
  uses one atomic operation, so an expired owner cannot delete its successor.
  LocMem performs the same checks/write under its backend's shared mutex.
  A crashed owner's lease expires after 60 seconds. Cache/script failures and
  unsupported atomic-cache backends are surfaced as incomplete monitoring;
  there is no unsafe check-then-write fallback. Other cache backends may still
  show current log evidence but cannot persist this probe's checkpoints safely.
  This does not connect to or execute scripts on SL Redis.
- No matching errors does not turn PostgreSQL green. Missing/unreadable files
  are shown as unavailable/incomplete monitoring.
- `HFL_SL_RUNTIME_LOG_DIR` optionally overrides the container-side directory.
  External SL deployments do not scan bundled logs unless explicitly configured.

## Automatic bundled queue measurement

Official HFL install/start/upgrade/recovery paths run the packaged
`configure-sl-queue-monitor.py` helper as a best-effort step. It locates exactly
one running SL Redis and API by Compose project/service/installation labels,
reads the effective broker URI without logging credentials, and adds only the
SL Redis to the existing `hyperfilelens-bridge`. The original private network
is preserved. The reserved alias is `hfl-sourcelens-redis`; generic service DNS
names (including `redis`, `postgres`, `nginx`) are rejected in both Aliases and
DNSNames. API/Worker/Scheduler/PostgreSQL network memberships remain unchanged.

Bridge members must belong to this installation's trusted HFL backend services
or its SL Nginx. Unknown workloads (including LensNode/other installations),
ambiguous service discovery, alias collisions and unsafe existing endpoints
cause a warning, never a core-service failure. No public ports, Docker socket
mounts, extra containers, timers, SL source/image changes or service restarts
are added. Internal Redis reachability expands to the trusted bridge; this
is not a Redis ACL/read-only account guarantee.

The helper writes `HFL_SL_RUNTIME_AUTO_REDIS_URL` before API startup for backwards
compatibility and publishes the authoritative hot configuration at
`data/runtime/sl-queue-monitor.json`. Backend containers mount this directory
read-only at `/opt/hyperfilelens/runtime`; it is not mounted into Nginx/Web and
is not in logs, media, or a public directory. The directory is 0700 and the file
is atomically replaced with 0600 permissions. The running API reopens the file
on requests; broker/database/credential changes alter both cache keys without
restarting API or changing its environment. An empty tombstone overrides stale
process environment; an invalid, non-private, symlinked, or unreadable file
fails closed. Explicit URL overrides still win; external mode ignores this file.
The authoritative hot file is published before the compatibility `.env` update.
If hot publication fails, the old `.env`/hot state stays intact and only this
invocation's new attachment is rolled back. If hot publication succeeds but
the compatibility update fails, the published endpoint and its attachment stay
active; the running API uses the hot file, not the old environment.

It records only attachment ownership (not credentials) in
`deploy/sl-queue-monitor.json`. Configuration/marker writes are atomic with
0600 permissions. SL recreation is reconciled after its independent upgrade;
ordinary startup and recovery also reconcile. Explicit
`HFL_SL_RUNTIME_REDIS_URL` overrides always win. External mode/uninstall clears
automatic configuration and disconnects only an attachment the helper created;
pre-existing user attachments are preserved. A transient Docker/service-discovery
failure keeps the last verified attachment, marker, and configuration untouched.
Only a new attachment created/requested in that invocation is rolled back after
failure. A definitive safety rejection (unsafe aliases/untrusted workloads or
unsupported broker target) disables automatic configuration and disconnects
only installer-owned attachment. Explicit cleanup and rejection attempt all
cleanup steps even if publishing one configuration file fails.
A missing helper or failed setup warns rather than blocking installation/upgrade.
Manual container recreation
outside HFL's lifecycle can lose the extra attachment; normal HFL start restores
it. Developer/custom SL layouts are not silently treated as owned deployments.

Queue queries remain on-demand: PING and LLEN in one non-transactional pipeline,
no message reads, scans or consumption. Connect/read timeouts remain two seconds,
with retries disabled. Successful results are cached independently for 300
seconds; probe/permission/config failures for 60 seconds. Single-flight cache
leases avoid overlapping queries and cache/lease failures do not cause unlocked
upstream polling. The parent 30-second health snapshot cannot extend queue
expiry. No scheduler task or host timer runs when nobody opens the page.

The source URL, queue names and threshold are hashed into the queue cache key;
endpoints, credentials and raw Redis errors never go to the browser. Unsupported
atomic cache backends or unavailable cache scripting are explicit monitoring
failures, not unsafe writes. `tools/quality/test-sl-queue-network.sh` exercises
actual Docker DNS and Redis queries/cache Lua using disposable isolated
containers/networks, never the running installation. Actual install/upgrade
lifecycle acceptance remains a deployment check.

- No automatic bundled endpoint: a deployment-setup Warning, not a false zero.
- Successful PING: Redis Health Check remains Healthy even if LLEN returns
  NOPERM/WRONGTYPE. Each failed queue is a separate metrics Warning; successful
  counts are retained.
- At/above `HFL_SL_RUNTIME_QUEUE_WARNING` (default 1000): backlog Warning and
  Degraded availability. Queue size alone does not prove a stopped Worker.
- Counts below the threshold appear as ordinary Details, with the actual Redis
  sample timestamp, not the page's newer API probe time.
- External/custom SL access can still use an explicit broker URL and approved
  internal connectivity. Do not reuse HFL's `REDIS_URL` or publish SL Redis.

## Summary and UI

Environment and Integrations use the same 30-second cached snapshot. Existing
API failure semantics remain; an otherwise ready API plus active index/queue
alerts changes overall SourceLens and Instance Health to Degraded. Recent
index evidence makes the SourceLens Health Check Unhealthy, without claiming
complete business unavailability. Historical/info notices do not act as live
failures, except retained incidents whose recovery is unconfirmed. Incomplete
scanning/checkpoints and metrics-access failures also make the overall summary
Degraded rather than silently declaring health. Snapshot cache failure falls
back to probing; log checkpoint failure is surfaced explicitly.

Reuse RuntimeStatusTable notices and HflStatusTag tones; no new CSS, columns,
cards, icons or task views. English, Simplified Chinese and Spanish copy is
included. Reloading the existing page refreshes data (within the cache TTL).

Live acceptance requires verifying mounted-log read permissions, the automatic
bundled attachment/DNS isolation, and queue warnings after install/upgrade.
Queue sample age is visible; core services must remain healthy if setup fails.
