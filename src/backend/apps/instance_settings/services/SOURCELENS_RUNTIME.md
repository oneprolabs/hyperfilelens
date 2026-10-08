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

## Optional queue measurement

`HFL_SL_RUNTIME_REDIS_URL` is deliberately empty by default. Do not reuse HFL's
`REDIS_URL`: this must address the SL **broker database**. No endpoint or
credential is returned to the browser.

```dotenv
HFL_SL_RUNTIME_REDIS_URL=redis://<internal-sl-redis-host>:6379/0
HFL_SL_RUNTIME_QUEUES=lens,sourcelens
HFL_SL_RUNTIME_QUEUE_WARNING=1000
```

HFL executes only PING and LLEN via a non-transactional pipeline. It never reads
message bodies or consumes a task. Network connect/read timeouts are two
seconds each, with retries disabled. Redis URL query options are rejected to
prevent overriding those limits. Queue names are deduplicated and limited to
eight. The warning threshold must be a positive integer.

The URL requires internal connectivity. In the stock layout only SL Nginx is
on the HFL bridge; SL Redis is on its separate private network. An operator
must provide approved internal access to the existing Redis endpoint, such
as an **HFL-side** Compose override attaching its API containers to the existing
SL network. Verify the actual project/network name and avoid the generic
`redis` hostname, which also names HFL's Redis. Do not expose Redis publicly,
mount Docker socket, edit SL code/images, or change SL services for this feature.
Use existing restricted credentials if the deployment supports them. This
patch does not automatically alter networks, invent an ACL account, or deploy
configuration to a shared environment.

- No URL: Not Monitored with an informational notice, not a zero queue depth.
- Successful PING/LLEN: Redis Health Check is Healthy.
- A queue at/above the threshold: yellow Warning with its name/count/threshold;
  Business Availability is Degraded. Queue depth alone does not prove a worker
  has stopped or a database is damaged.
- Below threshold: no backlog warning; queue size alone does not establish
  business readiness.
- Pipeline replies are processed individually (`raise_on_error=False`). A
  successful PING keeps Redis Healthy even when LLEN returns NOPERM/WRONGTYPE.
  Failed queue metrics receive their own Warning with no fabricated count;
  successful metrics for other queues are still retained. LLEN failure alone
  does not set Business Availability to unavailable/degraded.
- PING permission failure: Unknown health with a monitoring Warning, not a
  claim that Redis is down. Transport/connect/read failures indicate an
  unhealthy connection. Invalid configuration also receives a monitoring Warning.

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

Live acceptance requires verifying read permissions on the mounted logs and
configuring the optional SL Redis connection. Until that connection exists,
the page honestly reports that queue monitoring is not configured.
