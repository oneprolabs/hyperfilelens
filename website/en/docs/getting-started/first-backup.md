---
title: Create and run the first backup
description: Assign the target repository, review the configuration, and run the first backup.
---

# Create and run the first backup

This walkthrough uses the Huawei Cloud OBS repository created in the previous step and backs up all content under `C:\HFL-Quickstart`. Start with the small test directory before expanding the scope.

## Assign the target repository

1. Return to the **Target** step and select the edit icon on the source row.
2. In **Select Target Repository**, select the Huawei Cloud repository you created.
3. Select **OK**.
4. Confirm that the target column shows the repository, **Object Storage**, and **Online**.

![Select the created and online Huawei Cloud repository with the account and repository name blurred](/docs/getting-started/select-target-repository.png)

![Target repository assigned to the Windows source with the account, host, and repository name blurred while the public Endpoint remains visible](/docs/getting-started/assigned-target-repository.png)

## Optional restore plan

Select **Next** to open **Restore Plan**. A restore plan is optional and presets the restore scope, target node, destination directory, and whether same-name files are skipped or overwritten.

Enable a restore plan when the same restore rule will be reused. For temporary restores, historical versions, or changing the destination, use a manual restore instead. For this first backup, leave the plan unconfigured and select **Next**.

![Optional Restore Plan configuration with the restore target host blurred while the restore path and conflict policy remain visible](/docs/getting-started/optional-restore-plan.png)

## Review and create the configuration

1. On **Review**, check the source, `C:\HFL-Quickstart`, target repository, compression, policy, filter, and restore-plan settings.
2. When the summary is correct, select **Create**.

![Backup configuration summary on Review with the account, host, and repository name blurred while the restore path and configuration labels remain visible](/docs/getting-started/review-backup-configuration.png)

After creation, the wizard returns to **Start Backup**. The table lists the backup path, target repository, connectivity, and task status.

## Run the first backup

1. On **Start Backup**, confirm that the target repository **Connectivity** is **Online**.
2. Select the Windows backup source.
3. Select **Backup Now**.
4. Monitor the task in the **Backup Task** column.
5. Wait for the task status to become **Succeeded**.

![Backup configuration ready to run with Connectivity Online and the account and host information blurred](/docs/getting-started/backup-ready-to-run.png)

![First backup completed with Backup Task Succeeded and the account, host, and repository information blurred](/docs/getting-started/backup-succeeded.png)

Do not close the Windows Agent or change the repository credentials while the task is running. Continue to task and snapshot verification only after **Backup Task** shows **Succeeded**.

Next: [Check tasks and snapshots](/en/docs/getting-started/verify-backup).
