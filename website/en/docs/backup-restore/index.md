---
title: Backup and Restore
description: Protect data from a backup source, verify snapshots, and restore files and directories.
---

# Backup and Restore

The HyperFileLens data-protection workflow starts with a readable backup source, writes data to target storage through a backup configuration, and creates snapshots. An actual restore then confirms that the protected data is usable. A successful task confirms that execution finished; snapshot contents and restore results show whether the expected data can be recovered.

## Workflow

1. [Manage backup sources](/en/docs/backup-restore/sources) and confirm that the Agent or Proxy is online and the intended folders can be browsed.
2. [Manage target storage](/en/docs/backup-restore/targets), create a dedicated repository, and validate its connection.
3. [Create and run a backup](/en/docs/backup-restore/create-backup), selecting its scope, repository, and run options.
4. [View tasks and snapshots](/en/docs/backup-restore/snapshots) to check both the task result and the files in the snapshot.
5. [Restore files and directories](/en/docs/backup-restore/restore) to an independent location and inspect the restored content.
6. After validating the basic path, configure [policies and retention](/en/docs/backup-restore/policies) to meet the recovery point objective.

## Three checks that matter

- **The source is readable:** The Agent or Proxy is online, and the selected folders exist and are readable.
- **The target is writable:** The object storage, NAS, or local repository passes validation and uses a dedicated location.
- **The snapshot is recoverable:** The task finishes, the snapshot contains the expected folders, and an actual restore succeeds.

A **Partially Succeeded** task does not mean that all data is protected. Review failed directories, skipped items, and the actual data size before deciding whether the snapshot satisfies the recovery requirement.

![Completed first backup with Backup Task showing Succeeded and account, host, and repository details blurred](/docs/getting-started/backup-succeeded.png)

## Recommended first validation

Use a small set of synthetic test files for the first end-to-end run. Do not begin with an entire system drive, a production share, or a large directory. The validation is complete when:

- the backup source and target repository are both **Online**;
- **Backup Task** is **Succeeded**;
- the snapshot is **Available** and contains the expected files;
- one test file can be restored to an independent directory and **Restore Task** is **Succeeded**;
- the restored file opens and matches the source content.
