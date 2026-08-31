---
title: Restore Files and Directories
description: Create a restore task from an available snapshot and validate the recovered data.
---

# Restore Files and Directories

You can run a preset restore plan or create a new manual task. Use a manual task when the snapshot, scope, destination node, destination folder, or conflict policy needs to change.

For the first validation, restore only a small synthetic file set to an independent test directory and select **Skip**. Do not point a test restore at the source directory or overwrite existing files.

## Before starting

- The source has at least one successful or partially successful snapshot.
- The snapshot contains an available physical directory.
- The destination Agent is online, and the destination directory is writable.
- The destination disk has enough free space.
- The conflict policy is explicitly set to **Skip** or **Overwrite**; use **Skip** when uncertain.

## Create a manual restore

1. On **Start Backup**, select the source and choose **Restore**.
2. Select **Create New Restore Task**. **Run Restore Plan** immediately uses the latest snapshot, scope, destination, and conflict policy saved in the backup configuration.

![Two restore modes in Create Restore Task with host and IP information blurred while snapshot time, size, restore path, and policy remain visible](/docs/getting-started/choose-restore-mode.png)

3. Under **Backups & Snapshots**, select the backup and snapshot point.
4. Under **Restore Targets**, select an online destination node.
5. Select the whole snapshot, a directory, or individual files for each backup.
6. Choose the destination directory and check every source-to-destination mapping.
7. Select the conflict policy:
   - **Skip** keeps an existing destination file with the same name.
   - **Overwrite** replaces an existing destination file with snapshot content.
8. On **Review**, verify the snapshot, destination node, restore scope, destination paths, and conflict policy, then select **Start Restore**.

![Single test file mapped to an independent restore directory with personal host and path identifiers blurred while synthetic names remain visible](/docs/getting-started/map-restore-file.png)

![Single-file restore task on Review with host and IP information blurred while snapshot time, restore path, and conflict policy remain visible](/docs/getting-started/review-restore-task.png)

**Overwrite** can replace current files. Use it only after independently verifying the destination, path, and current content. Routine validation should use **Skip**.

## Use a restore plan

A restore plan uses the scope, destination, and conflict policy saved with the backup configuration and runs from the latest available snapshot. Sources without a configured restore plan are skipped, and the interface shows the effective run scope before submission.

Plans suit a fixed destination and repeatable recovery exercise. Use a manual task when the destination changes or when you need a different snapshot or subset of files.

## Verify the result

After the task finishes, inspect the actual files on the destination host instead of relying only on the console status. A stopped task can leave incomplete files in the destination.

Confirm that **Restore Task** is **Succeeded** on **Start Backup**, then open **Restore Records** in the source details. Check the record status, file-item status, restored count, and destination path.

![Successful single-file restore in Restore Records with host and IP information blurred while Record, Task, and Snapshot identifiers and times remain visible](/docs/getting-started/restore-record-succeeded.png)

For failures, record the failed path, destination node, error code, and task time, then see [Backup, Storage, and Restore troubleshooting](/en/docs/troubleshooting/protection).
