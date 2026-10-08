# SourceLens Runtime Environment alerts (HFL only)

This is a small addition to the existing Runtime Environment table, not a
container monitor or an SL patch. Runtime State remains Not Monitored. API
readiness remains independent of database log evidence and queue warnings.
There are no Chat/task IDs, task payloads, Docker socket access, repairs or queue
deletions.

## Index error evidence

Bundled deployments already mount `data/sourcelens/logs` read-only at
`/var/log/sourcelens` in both development and production HFL Compose files.
HFL inspects PostgreSQL and Worker log tails for `unexpected zero page` and
`right sibling's left-link doesn't match`.

- Maximum four files per directory, 256 KiB per file, 512 directory entries.
- Regular files only; no symlink files. The scan is best-effort, not a complete
  historical search or an index integrity check.
- PostgreSQL/Celery event timestamps are used, not file modification times.
  Naive timestamps are interpreted as UTC, matching the packaged SL runtime.
- Errors within the last 24 hours produce a red Error notice and Unhealthy
  Health Check on PostgreSQL. Business Availability remains unknown: an index
  error does not prove that every database query fails.
- Older/undated evidence produces an informational notice explicitly saying
  current integrity is not confirmed. Aging out does **not** mean repaired.
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
- Probe/config failure: explicit monitoring warning; no fabricated counts.

## Summary and UI

Environment and Integrations use the same 30-second cached snapshot. Existing
API failure semantics remain; an otherwise ready API plus active index/queue
alerts changes overall SourceLens and Instance Health to Degraded. Recent
index evidence makes the SourceLens Health Check Unhealthy, without claiming
complete business unavailability. Historical/info notices do not act as live
failures. Cache failures fall back to probing rather than hiding diagnostics.

Reuse RuntimeStatusTable notices and HflStatusTag tones; no new CSS, columns,
cards, icons or task views. English, Simplified Chinese and Spanish copy is
included. Reloading the existing page refreshes data (within the cache TTL).

Live acceptance requires verifying read permissions on the mounted logs and
configuring the optional SL Redis connection. Until that connection exists,
the page honestly reports that queue monitoring is not configured.
