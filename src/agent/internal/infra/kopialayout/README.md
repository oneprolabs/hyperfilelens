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

This change adds no cache-size policy, low-space threshold, relocation UI or
automatic backup blocking.

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
