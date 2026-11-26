---
title: Repository cache settings
description: Configure local Kopia repository caching for Agents and Proxies, including capacity, allocation, application timing, and disk-space planning.
---

# Repository cache settings

<p class="hfl-doc-lead">Agents and Proxies use local Kopia caches to reduce repeated reads from backup repositories. Adjust the cache budget to suit the host's available disk space and number of repository connections without reinstalling the component.</p>

## Scope

This setting applies to each managed repository connection on the selected Agent or Proxy. It is not a shared allowance for all repositories on the host. When several hosts access the same repository, each host uses its own local cache. Separate repository connections on one host can also consume space independently.

Repository caches use local disk space. This setting is not a memory quota, target-storage capacity, or an installation-package download cache. Private Data Gateways do not expose this setting; their Insights runtime resources are outside the scope of this guide.

## Configure the cache

1. Open <span class="hfl-path">Protection → Production Sites</span> and select <span class="hfl-ui">Source Hosts</span> or <span class="hfl-ui">Proxy Hosts</span>.
2. Open the host details and select <span class="hfl-ui">Advanced Settings</span>.
3. Under <span class="hfl-ui">Repository Cache</span>, change <span class="hfl-ui">Kopia cache per repository</span>.
4. Review the other resource settings on the page, then select <span class="hfl-ui">Save Changes</span> in the drawer footer. Saving submits the resource settings together.

| Item | Behavior |
| --- | --- |
| Unit | MB, entered as a whole number |
| Default budget | The console and normal managed repository tasks use `8192 MB` by default. Existing connections receive the setting during a subsequent repository operation |
| Allowed range | `0–65536 MB` |
| Scope | Each managed repository connection on the selected host |
| Set to `0` | Disable content and metadata caching. This does not disable every cache type or immediately delete existing cache files |

If a custom value has already been saved, use the value shown in the console. The default above applies to console-managed repository tasks, not to standalone Kopia commands.

## How the budget is divided

For a nonzero budget, the Agent automatically divides the allowance between content and metadata caches, normally in an approximately **75% / 25%** split:

- **Content cache**: holds data content read from the repository.
- **Metadata cache**: holds metadata used to access the repository and its snapshots.

The content allocation is three quarters of the total, rounded down to a whole MB, with a minimum of `1 MB`. Metadata receives the remainder. The console setting controls the combined budget; it does not let you change the two percentages independently.

| Configured value | Content-cache budget | Metadata-cache budget |
| --- | --- | --- |
| `0 MB` | Disabled | Disabled |
| `4096 MB` | `3072 MB` | `1024 MB` |
| `8192 MB` | `6144 MB` | `2048 MB` |
| `16384 MB` | `12288 MB` | `4096 MB` |

**The 75% / 25% split divides the cache budget. It is not a percentage of backup data, host memory, or total disk capacity.** The system does not calculate this setting automatically from those capacities, and no single cache-to-data ratio suits every dataset.

## Application and verification

A successful save confirms that the settings were submitted, not that every repository connection has already changed. The new budget applies during the next safe managed repository operation. Running jobs and long-lived repository services are not forcibly restarted to apply it.

1. Confirm that the Agent or Proxy is online.
2. Wait for a subsequent job on that host that accesses the repository, such as a backup or restore.
3. Check the Agent-reported application status in Advanced Settings. Distinguish <span class="hfl-ui">Pending</span>, <span class="hfl-ui">Applied</span>, and <span class="hfl-ui">Not applied / degraded</span>.
4. Review the job result and the host's actual free disk space to decide whether the budget is appropriate.

Cache status describes the most recently confirmed managed connection. It does not mean that every historical cache has been resized or cleared. If application fails, the existing connection configuration is retained and the status is reported. Do not repeatedly reinstall or re-register the host to resolve a cache-setting issue.

## Capacity planning

- **Evaluate the default first**: when disk space permits, start with `8192 MB` and review job duration, repository access, and disk usage before adjusting it. A larger budget does not guarantee faster jobs.
- **Count separate connections**: three independent connections on one host, each set to `8192 MB`, have a combined content-and-metadata budget of `24576 MB`. They do not share a single `8192 MB` budget.
- **Leave additional free space**: index and other caches, logs, temporary files, and restore output can also consume disk space. The setting is not a hard disk limit for the entire cache directory or Agent installation. Do not allocate all remaining disk space to it.
- **Reduce the budget cautiously on constrained hosts**: a smaller budget or `0` can increase remote reads. Verify actual job behavior after changing it. Reducing the budget does not mean that existing cache files immediately release disk space.
- **Avoid a fixed percentage of backup data**: plan around the actual connection count, workload, and available disk space rather than reserving the same cache-to-data ratio for every backup.

## Cache directories and safety

The current layout places reconstructible repository caches under `cache/repositories/` within the Agent installation root. Connection configurations live under `data/kopia/repositories/`. Installation identity, credentials, and the task database are not disposable cache files.

Do not manually clear caches while jobs are running. Do not delete the entire Agent directory, connection configurations, or migration records to free space. During an incomplete upgrade from an older cache layout, historical cache directories can still contain configuration that must be preserved; do not delete those directories wholesale.

If a job fails, a component goes offline, or an upgrade encounters a problem, review the job details first, then follow [Installation and nodes](/docs/troubleshooting/installation-nodes) or [Backup, storage, and restore](/docs/troubleshooting/protection).
