---
title: Backup scope and system paths
description: How HyperFileLens treats host root and system paths during file-level backup.
---

# Backup scope and system paths

HyperFileLens protects host backup sources with file-level backup. You can include `/` or a drive root in the backup scope, but the Agent automatically excludes certain operating-system runtime paths under built-in boundary rules. Whether other content enters the snapshot still depends on read permissions, file types, and the backup policy and file filter.

This page describes expected behavior in the current release. For choosing directories, see [Manage backup sources](/docs/backup-restore/sources). For policy and filter settings, see [Policies and retention](/docs/backup-restore/policies).

## File-level backup and the root path

On Linux, Windows, or macOS hosts, you can select a root path as the backup source directory. This matches the boundary in [Limitations and security](/docs/reference/limitations-security):

- File-level backup does not automatically create application-consistent copies of databases, virtual machines, or business applications.
- It is not a system-disk image suitable for bare-metal restore.

When you back up a parent path that covers the Agent root, that directory is excluded so product runtime files are not written into the snapshot. For day-to-day protection of business data, prefer explicit data directories instead of the entire root tree.

## Automatically excluded system paths

When the backup source covers the paths below, the Agent adds ignore rules from the built-in boundary list and records the excluded system paths in the result. These exclusions are expected and do not mean the job failed.

| Platform | Automatically excluded |
| --- | --- |
| Linux | `/proc`, `/sys`, `/dev`, `/run` |
| macOS | `/.vol`, `/dev`, `/private/var/run`, `/private/var/vm`, `/System/Volumes/VM` |
| Windows | `System Volume Information`, `pagefile.sys`, `hiberfil.sys`, `swapfile.sys`, `DumpStack.log.tmp` |

You cannot select one of these protected paths itself as the backup source. Selecting `/proc` alone, for example, is rejected.

If the Agent is configured with additional system boundary paths, those paths are also excluded when the source covers them.

## `/lib` and ordinary system directories

Directories such as `/lib` and `/lib/modules` are not on the automatic exclusion list above. They are treated as ordinary directories. Regular files the Agent can read can enter the snapshot.

Selecting a root path—or a tree that includes `/lib`—therefore does not guarantee a complete `/lib/modules` tree in the snapshot. Rely on the snapshot contents and job details.

## How policies and filters affect the result

If expected content is missing from a snapshot, the cause is usually configuration or runtime conditions, not a hard product block on ordinary system directories such as `/lib`:

| Setting | Effect |
| --- | --- |
| **Current Filesystem Only** in the file filter | Scans only the filesystem that contains the source path; content on other mount points is left out of the snapshot |
| Exclude rules, maximum file size, and cache-directory options in the file filter | Matching content is not included in later snapshots |
| Handling of unreadable directories and unreadable files in the backup policy | When skip is enabled, items the Agent cannot read may be omitted |
| Handling of unsupported filesystem entries in the backup policy | When skip is enabled, sockets, device nodes, and similar special entries may be omitted; when it is off, the same entries can appear as errors in the job |

Policy and filter changes affect later snapshots only. They do not add files that were never included in an existing snapshot.

## Verification recommendations

A successful or partially successful backup job only means the job finished under the active policy and boundary rules. Content that was automatically excluded, filtered out, or skipped by policy does not appear in the snapshot.

If you rely on critical files under a root or system path:

1. Open the snapshot browser and confirm that the paths and files you care about are present.
2. Review skipped items, errors, or diagnostics in the job details.
3. Restore a representative file to a separate directory and confirm that the content is readable.

Do not treat job success alone as proof that the paths you care about were captured in the snapshot.
