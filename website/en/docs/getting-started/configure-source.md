---
title: Configure the backup source
description: Select the Windows host and add the test directory to the backup scope.
---

# Configure the backup source

This step selects the registered Windows host in the **Backup Wizard** and adds `C:\HFL-Quickstart` to the backup scope. Do not select the entire system drive.

## Open Backup Setup

1. In the **Backup Sources** table, select the registered Windows host.
2. Select **Next** to move to **Backup Configuration**.
3. Select the Windows host again, then select **Backup Setup**.

![Select the registered Windows source in Backup Configuration with the hostname, IP address, and account blurred while the registration time remains visible](/docs/getting-started/select-source-for-setup.png)

## Select the directory to back up

1. In **Sources**, expand the Windows backup source.
2. Under **Browse Files and Folders**, find and select `C:\HFL-Quickstart`.
3. Select **Add Selected**.
4. Confirm that `C:\HFL-Quickstart` appears under **Selected Paths**.
5. Select **Next**.

![C:\HFL-Quickstart added to Selected Paths with the hostname and IP address blurred](/docs/getting-started/select-backup-directory.png)

## Choose backup policy and file-filter settings

This test must back up everything in `C:\HFL-Quickstart`. Leave **Backup Policy** and **File Filter** unassigned, keep the current compression setting, and select **Next**.

For recurring backups, use **Backup Policy** to select or create a policy. To exclude content that must not enter a snapshot, use **Filter Rule** to select or create a file-filter rule. Policies and filters change subsequent backup behavior, so do not add an unverified rule during the first-use test.

![Optional Backup Policy and File Filter settings with the Windows hostname and account blurred](/docs/getting-started/optional-backup-policy.png)

## Completion criteria

- The correct Windows host is selected.
- The backup scope contains only the prepared test directory.
- `C:\HFL-Quickstart` appears under **Selected Paths**.
- No filter rule excludes either test file.

If no repository is available on the **Target** step, continue without leaving the configuration flow: [Add target storage](/en/docs/getting-started/add-target).
