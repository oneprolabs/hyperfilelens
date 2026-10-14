# Optional Agent performance governance

Policies are activated only by an explicit console save. Historical
`metadata.perf_settings` is not automatically activated on upgrade.

The dedicated, authenticated HTTP sync endpoint is independent of task admission,
WebSocket connectivity, and lifecycle fences. Revisions are idempotent and stale
revisions are ignored. The local cache is bound to node/org/installation identity.
Disk, network and application errors retain the prior runtime behavior.

## Scope and semantics

- CPU is an aggregate CPU-time budget for the Agent service/process group.
  Linux uses the dedicated service cgroup/controller (or authorized runtime
  systemd properties); Windows uses an inherited CPU Job. Initial Windows
  attachment waits for existing work processes to finish. No service restarts,
  sudo, host-wide settings or hard memory limits are used.
- Unsupported permissions, controllers or operating systems are reported rather
  than emulated with process priority. macOS does not advertise a verified CPU
  process-tree quota.
- Memory is a **soft sum-of-RSS budget**, not a hard physical-memory ceiling.
  Under pressure, new subprocess starts receive a bounded 200 ms pacing delay.
  Running tasks are not terminated. Shared mappings can be counted more than
  once; this metric is deliberately described as combined RSS.
- Backup upload bandwidth is shared across processes using a loopback-only,
  per-instance authenticated coordinator. Patched Kopia paces object-storage/
  filesystem backup writes and repository-server content uploads. Proxy data
  writes share its node budget; restore reads and control operations do not.
  Quota grants are FIFO among live tasks, chunked at 64 KiB with a bounded burst.
  This is application data pacing, not a NIC-level instantaneous guarantee.
  Hash/retry reads and provider buffering can make accounting conservative.
  IPC errors and a 30-second per-operation pacing safety bound fail open.
- Cache retains its existing per-repository semantics and next-operation timing.
- Log level, retention and space apply to registered Agent/runtime-server log
  writers, not audit records, task history, system logs, installer logs or AI
  engine container logs. Writes are bounded asynchronous best effort. Only
  exact owned rotation files (or closed registered runtime logs) are cleaned;
  active logs and symlinks are not deleted.
  The total soft log budget is split equally between Agent/runtime-server logs
  and managed Kopia CLI/content logs. Bundled Kopia uses the selected log level
  and bounded segments; the Agent cleans inactive segments after checking live
  PIDs and file identity. Retired server logs are rediscovered after restart.

## Status and rollout

The console distinguishes saved settings from Agent reports: pending, applied,
unsupported, degraded, or soft-budget monitoring. Cache reports describe the
latest managed connection, not an instantaneous eviction of all old caches.
Bandwidth is pending until a managed transfer uses it; an old Kopia is reported
unsupported. Existing long-lived server processes pick up the environment on
their next safe creation, not by forced restart.

Verify each supported OS/install mode on a real host before production rollout.
Cross-compilation is not a CPU-controller/Job integration test. Failure injection
must include unavailable policy APIs, unwritable local state, IPC failure, log
I/O/cleanup failures, stale revisions and concurrent backup uploads. No policy
failure is allowed to become a business-task error.
