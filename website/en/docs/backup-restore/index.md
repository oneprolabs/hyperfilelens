---
title: Backup and restore
description: Protect data from a backup source, verify snapshots, and restore files and directories.
---

# Backup and restore

The HyperFileLens data-protection workflow starts with an accessible backup source. A backup configuration writes the selected data to target storage and creates snapshots. A restore test then confirms that the protected data can be recovered. A successful task means that the job finished; the snapshot contents and restore results show whether the expected data is available.

## Workflow

1. [Manage backup sources](/docs/backup-restore/sources) and confirm that the Agent or Proxy is online and the intended folders can be browsed.
2. [Manage target storage](/docs/backup-restore/targets), create a dedicated repository, and validate its connection.
3. [Create and run a backup](/docs/backup-restore/create-backup), selecting its scope, repository, and run options.
4. [View tasks and snapshots](/docs/backup-restore/snapshots) to check both the task result and the files in the snapshot.
5. [Restore files and directories](/docs/backup-restore/restore) to an independent location and inspect the restored content.
6. After validating the basic path, configure [policies and retention](/docs/backup-restore/policies) to meet the recovery point objective.

## Three checks that matter

- **The source is readable:** The Agent or Proxy is online, and the selected folders exist and are readable.
- **The target is writable:** The object storage, NAS, or local repository passes validation and uses a dedicated location.
- **The snapshot is recoverable:** The task finishes, the snapshot contains the expected folders, and a restore test succeeds.

A **Partially Succeeded** task does not mean that all data is protected. Review failed directories, skipped items, and the amount of data captured before deciding whether the snapshot meets your recovery requirements.

![Completed first backup with Backup Task showing Succeeded and account, host, and repository details blurred](/docs/getting-started/backup-succeeded.png)

## Recommended first test

Use a small set of synthetic test files for the first end-to-end run. Do not begin with an entire system drive, a production share, or a large directory. The validation is complete when:

- the backup source and target repository are both **Online**;
- **Backup Task** is **Succeeded**;
- the snapshot is **Available** and contains the expected files;
- one test file can be restored to an independent directory and **Restore Task** is **Succeeded**;
- the restored file opens and matches the source content.
