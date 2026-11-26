# Disposable Kopia cache layout

The installation's platform-aware Agent Root remains authoritative. System and
user installations use the same relative layout:

| Content | Relative location |
| --- | --- |
| Connection configs, maintenance configs and connection fingerprints | `data/kopia/repositories/`, with historical filenames unchanged |
| Agent repository preparation locks | `runtime/kopia/repositories/` |
| Migration/usage coordination lock | `runtime/kopia/migration.lock` |
| Durable migration journal | `data/kopia/migrations/cache-layout-v1.json` |
| Kopia CLI and content logs | `logs/kopia/cli-logs/` and `logs/kopia/content-logs/`, or the configured log root |
| Reconstructible repository caches | `cache/repositories/`, with historical cache names unchanged |

Kopia's native `.mlock` maintenance lock has a fixed config-adjacent filename.
It remains beside the relocated config, outside cache. It is not redirected
through symlinks and does not require a modified Kopia binary. Neither native
maintenance locks nor Agent preparation locks may be unlinked while in use.

## Upgrade and recovery

Startup migrates before task dispatch. CLI operations use the same migration
and resolve old managed configuration references at their point of use.
Exact registered repository aliases are switched transactionally; historical
task payloads are not rewritten.

Migration requires exclusive use of the installation. New operations hold a
shared usage lock until their processes exit; detached repository servers and
asynchronous cleanup keep their own usage lock. Process inspection protects
against old Agents and Kopia processes that do not know the new lock.

Use the incoming package's installer for the first upgrade. Its pre-stop checks
defer an upgrade while repository work is active. A historical installed
installer cannot gain this new check retroactively. Do not start an old Agent,
invoke Kopia manually, or start another installer against the same installation
during migration. Downgrading to the historical cache-based layout is not
supported.

Each known file is recorded, copied outside cache, verified and made private
before references switch or the old copy is removed. Native config JSON is
preserved apart from rebasing its cache path; no new credential store is added.
Cache budgets, repository passwords, client identities and retention settings
are unchanged. Interrupted runs resume from the private journal. Conflicting
targets are not overwritten.

Inventory is restricted to known generated filenames, validated connection
envelopes, associated sidecars, Kopia log namespaces and cache structures.
Unknown files, links, unexpected content and over-bound files are retained.
An incomplete inventory does **not** authorize deleting the cache root. Known
connections may still use the new layout, while the retained items require
inspection. Inventory is bounded to 250,000 entries; config files to 8 MiB and
historical log segments to 256 MiB.

Inspect non-secret migration metadata with:

```sh
hfl-agent cache-layout status --data-dir /absolute/path/to/agent-root
```

Only `status: completed` with `disposable: true` reports a completed migration.
The private journal must not be removed to work around a migration failure.
Do not share native configs or the private journal through logs or API output.

## Cache deletion boundary

After migration completes, stop all cache users before deleting the managed
cache root. The next operation rebuilds it without re-enrollment or a new
repository connection. Existing snapshots remain in their actual repositories.
Deleting cache during an operation can fail that operation; it must not delete
identity, credentials, durable tasks, restore output or valid backups.

## Demand-driven cache management

Nodes explicitly advertising `cache_management_v1` use the patched Kopia
`cache-demand-driven` mode. Content and metadata caches grow on demand without
fixed capacities or a content/metadata allocation ratio. Ordinary restore,
download and other content reads remain cached. Invalid data and explicit cache
invalidation retain their existing behavior. Upgrade does not clear warm caches.
Legacy Kopia's zero-size arguments still disable caching; zero is NOT unlimited.

Capability probes cache only successful help invocations, including a confirmed
absence of the feature. Execution failures and cancellations remain retryable;
concurrent callers share observations by binary identity. A failed probe is not
evidence that an already enabled installation has reverted to legacy mode.
Valid persisted policies retain their mode and backup-space protection without
authorizing new policy changes on an unverified binary. Confirmed incompatible
binaries require a compatible Agent/Kopia package rather than silent downgrade.
Implicit defaults are not persisted opt-in; malformed policies fail closed.
Inventory keeps applied-mode evidence separate from verified binary capability.

Each installation has a separate versioned cache policy. The backup-blocking
threshold B defaults to 10 GiB; warning W is B + 10 GiB (20 GiB by default).
One GiB is 1,073,741,824 bytes. Existing byte-valued policies remain readable
and are never rewritten on read.
The `cache_policy_gib_v1` capability gates new binary-delta saves; older
cache-management nodes retain their decimal defaults until upgraded. The
protocol stores exact integer byte values and
persists B/W/revision together outside cache. CPU, memory, bandwidth and log
settings are independent. An unsupported Agent/Proxy keeps its existing
fixed-size configuration and editor; the console explains the limitation and
provides upgrade guidance. A mixed-version backup chain is protected only on
the individual nodes that support and have applied the new mode.

Before backup-related repository preparation and again before snapshot
execution, the executing account samples available bytes on the active cache
filesystem (Unix `Bavail`, Windows `freeAvail`). Available >= W proceeds;
B <= available < W warns and proceeds; available < B fails this task. A query
failure is distinct from insufficient space. Checks do not disable a shared
policy or other nodes. Each near-limit check records a Backup Space Check
Warning through the existing EVENT rule matcher. Insufficient space and query
failures follow only the existing Backup Failed lifecycle with a structured
reason, without a duplicate cache warning. Query failure has unknown,
not zero, current free space. Events are retained per execution; notification
merge and silence belong to the existing alert rule framework, not an Agent
or cache-specific state controller. Failed historical tasks retain their normal
manual/explicit recovery semantics.

**Preflight only:** there is no runtime monitoring, cancellation or space
reservation. Passing a check cannot prevent cache or business writes from
exhausting the filesystem later. No backup/check means no immediate-space alert.

### Active root and relocation

Persistent cache-management records are under `data/kopia/cache-management/`;
never publish their private journals, which can contain connection recovery
copies. The active-root manifest, policies, journals, credentials, configs,
node identity and task state remain outside cache. Stable connection cache
names are independent of the selected root. Unexpected removal of the owned
cache recreates it only on its verified parent filesystem.

The operator selects an existing writable parent directory. Agent validation
creates no pending path switch; it returns short-lived evidence for a dedicated
`.hyperfilelens-cache-<installation-id>` child. Critical paths, protected-state
and repository overlaps, aliases/reparse points, changed mounts and arbitrary
non-empty unowned directories are rejected. Validation must complete online
before the operator confirms the durable `cache.relocate` task.

Selecting the configured node root or its canonical `cache` path restores the
exact default cache subtree, rather than adding another hidden child. Only
that canonical target is exempt from the broad installation-root overlap rule;
logs, runtime, persistent data, real repositories, unowned files, aliases and
low available-space checks still apply. Validation and execution recheck the
same resolution. Returning to default never adopts the entire installation.

The task closes admission, drains existing users without killing them,
revalidates, prepares the owned destination, journals connection references,
commits one active root, verifies it, and only then inventories/deletes old
managed cache files. Verification checks managed cache references, directory
identity and a temporary local read/write probe. It deliberately does not open
repositories or run `kopia cache info`: those operations need transient backup
credentials and repository availability unrelated to moving local cache. No
password is stored or requested by relocation. Shutdown/stop operations remain available during draining;
already admitted nested clients and detached servers retain their usage leases.
Startup recovery uses the same local verification, without resolving or starting
Kopia and without requesting repository passwords or temporary credentials.
Unclassified content is retained. New cache is warmed again, not copied, so
relocation can cause additional downloads.

A pre-verification failure rolls references back and retains the old root.
A verified cutover with failed old cleanup reports `switched_cleanup_pending`;
normal operations use the new root and cleanup can be retried idempotently.
Interrupted switching recovers before task admission. Never roll back into a
partially deleted old root or delete data to force a failed task to complete.

The console reports cache usage as logical regular-file bytes, not allocated
blocks. Local walks have entry/time budgets, do not follow links, deduplicate
hard links and show timestamps/completeness. Heartbeats do not scan cache trees
or object storage. UI status refresh samples do not fabricate preflight
recovery events.

## Isolated verification

With a verified bundled Kopia for the current platform:

```sh
cd src/agent
HFL_TEST_KOPIA_BINARY=/absolute/path/to/kopia \
  go test ./internal/infra/kopialayout
go test -race ./internal/infra/kopialayout ./internal/platform/filelock
```

The real-Kopia test creates two temporary repositories and valid backups, checks
warm-cache reuse and durable alias migration, deletes the whole cache root, then
lists and restores the original snapshots. It never uses development or shared
backup data. Without `HFL_TEST_KOPIA_BINARY`, that integration test is explicitly
skipped rather than reported as having run.
