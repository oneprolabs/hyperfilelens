---
title: Repository cache management
description: Understand local metadata and content caches, configure cache protection, relocate the cache directory, and plan disk space for Agents and Proxies.
---

# Repository cache management

<p class="hfl-doc-lead">Agents and Proxies use local repository caches to avoid downloading or rebuilding the same repository data repeatedly. Current cache-management nodes grow these caches on demand and use a disk-space preflight to protect new backups.</p>

## What is cached

The repository engine keeps two useful classes of local, reconstructible data:

- **Metadata cache**: repository and snapshot metadata needed to locate, verify, and enumerate backup data. It is especially useful when a job repeatedly lists snapshots or accesses many small files.
- **Content cache**: repository contents read during backup, restore, download, browsing, and other content operations. Reusing warm content can reduce repeated remote reads.

The active cache is stored below the Agent installation root at `cache/repositories/`. Connection configurations and identities are separate: they remain in the Agent's protected data area and are not disposable cache data. Cache files are local to each Agent or Proxy; two hosts that access the same repository do not share a local cache.

Cache contents are a performance aid, not the backup itself. Removing a completed, managed cache causes later operations to rebuild it from the repository; it does not remove snapshots or require re-enrollment. Do not delete the Agent root, connection configuration, credentials, task database, logs, or restore output as a cache-cleanup shortcut.

## Current cache-management mode

An Agent or Proxy that advertises `cache_management_v1` uses HyperFileLens' managed **demand-driven** mode:

- content and metadata caches grow as operations need them;
- there is no fixed content/metadata capacity split and no configured cache-size hard limit;
- ordinary restore, download, browsing, and content reads continue to use the cache;
- existing warm cache data is reused, including after an Agent upgrade; and
- The legacy cache implementation's zero-size meaning still applies: `0` disables the corresponding legacy cache setting. It does **not** mean unlimited capacity.

Demand-driven growth does not reserve disk space and does not make the filesystem unlimited. The protection boundary is the Agent's backup preflight described below. A successful preflight cannot prevent later growth from logs, temporary files, restore output, or a running operation.

### Older-node compatibility

Nodes without `cache_management_v1` keep the older per-repository fixed-budget behavior. On those nodes, the legacy console setting is still interpreted as follows:

| Item | Legacy behavior |
| --- | --- |
| Scope | Each managed repository connection on the selected Agent or Proxy |
| Default budget | `8192 MB` |
| Allowed range | `0–65536 MB` |
| Allocation | Approximately 75% content cache and 25% metadata cache |
| `0` | Disables the legacy content and metadata cache settings; it does not immediately remove existing files |

Do not use this table to predict the behavior of a node that has `cache_management_v1`. The console keeps legacy settings available and shows an upgrade message when a node must be upgraded before the new cache-management controls can be used.

## Configure and inspect a managed cache

1. Open <span class="hfl-path">Protection → Production Sites</span>.
2. Select <span class="hfl-ui">Source Hosts</span> or <span class="hfl-ui">Proxy Hosts</span>, then open the host details.
3. Open <span class="hfl-ui">Advanced Settings → Cache management</span>.
4. Review the active cache directory, cache usage, filesystem free space, and the last sampled time.
5. Use <span class="hfl-ui">Refresh</span> when you need a new Agent-side measurement. The displayed timestamp is the latest sample, not continuous monitoring.

The managed panel exposes these controls when the node is online and supports the capability:

- **Blocking threshold**: available space below this value blocks the next backup preflight.
- **Warning threshold**: calculated as the blocking threshold plus `10 GiB`.
- **Change cache directory**: moves the managed cache root to another filesystem or directory parent.
- **Refresh**: requests a current usage and free-space sample.

A threshold change is saved as a separate cache-policy task and requires an online, routable node. It is not a cache-size setting. The next backup-related operation rechecks the persisted policy before repository preparation. Existing jobs are not forcibly restarted.

## Disk-space protection

The default policy uses binary GiB (`1 GiB = 1,073,741,824 bytes`):

```text
Blocking threshold: 10 GiB available
Warning threshold: 20 GiB available
Warning threshold = blocking threshold + 10 GiB
```

The check measures free space on the filesystem containing the active cache root, using the account that runs the Agent. For a backup-related operation:

| Available space | Result |
| --- | --- |
| `>= warning threshold` | Continue normally |
| `>= blocking threshold` and `< warning threshold` | Record a low-space warning and continue |
| `< blocking threshold` | Stop before backup/repository preparation |
| Space cannot be queried | Fail the space check separately; do not report a fabricated zero |

This is a **backup preflight**, not a reservation, a continuous monitor, or a hard quota for the cache directory. Space can be consumed after the check passes. If the operation is blocked, free space, expand the volume, or safely relocate the cache, then run a new backup. Lowering the threshold does not create disk space.

Plan for the expected cache growth **plus** logs, indexes, temporary files, restore output, and other workloads on the same filesystem. Treat `20 GiB` as the default warning headroom, not as a promise that 20 GiB is sufficient for every workload. Do not allocate all currently free space to cache growth.

## Relocate the cache directory

To move a managed cache, choose an existing, writable parent directory in <span class="hfl-ui">Change cache directory</span>. HyperFileLens creates an installation-owned child such as `.hyperfilelens-cache-<installation-id>`; it does not take over an arbitrary non-empty directory.

During relocation, the Agent waits for active cache users, validates the destination again, and switches the active root only after the destination is safe. The old cache contents are **not copied** to the new location. The new cache therefore needs to warm up again and may download repository data again. The old managed cache is cleaned only after the new root is active.

The destination must not be a system path, a real repository, a restore-output directory, a protected Agent state path, or a path that overlaps the old cache. If preparation or validation fails, the old root remains in use. If the new root is already active but cleanup of the old root fails, the UI reports **cleanup pending**: keep using the new root, resolve the filesystem or permissions problem, and retry cleanup instead of switching back manually.

Do not relocate while a backup, restore, repository service, or maintenance process is using the cache. Directory relocation changes cache placement only; it does not move snapshots, credentials, connection configuration, task history, or business data.

## Workload recommendations

| Workload | Recommendation |
| --- | --- |
| Many small files | Keep enough space for metadata growth and expect random metadata I/O. Measure repeated listing and restore jobs; a larger cache may help metadata reuse, but it is not a fixed 75/25 tuning target in demand-driven mode. |
| Large files | Keep enough space for repeated content reads and restore/download bursts. Check temporary restore output separately because it is not part of the cache quota or cache usage. |
| Mixed workloads | Use representative backup, browse, restore, and download jobs. Compare duration, remote reads, cache usage, and free space instead of assuming one universal cache-to-data ratio. |
| Public-internet object storage | Account for latency, repeated downloads, request volume, and provider egress or transfer charges. A warm local cache can reduce repeated reads, but it does not eliminate repository requests or network costs for new data. |

## Troubleshooting

- **`CACHE_SPACE_INSUFFICIENT`**: release disk space, expand the volume, or complete a safe cache relocation. Then start a new backup; changing the threshold alone does not retry a blocked operation.
- **`CACHE_SPACE_CHECK_FAILED`**: check that the active cache mount exists and that the Agent execution account can inspect it. A failed measurement is not the same as a zero-byte result.
- **Destination validation fails**: choose an absolute, existing, writable parent and avoid protected paths, repository paths, restore output, nested paths, and unmanaged non-empty directories.
- **Cleanup pending after relocation**: do not delete the Agent root or switch references manually. Fix the old-directory permission, mount, or locking issue and retry cleanup.
- **The timestamp looks old**: cache status is sampled on demand or during a managed operation; it is not a live filesystem monitor.

For node registration or connectivity problems, see [Installation and nodes](/docs/troubleshooting/installation-nodes). For a failed backup or restore, see [Backup, storage, and restore](/docs/troubleshooting/protection).
