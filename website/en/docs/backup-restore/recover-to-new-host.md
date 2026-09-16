---
title: Recover data to a new host after source host failure
description: Restore snapshots from an unreachable or permanently damaged source host to a new host with a fresh Agent installation.
---

# Recover data to a new host after source host failure

When the original backup source host is permanently damaged or offline, you can still recover your data from existing snapshots to a replacement host. The snapshots remain in the target storage repository and are available for restore as long as the source node, repository, and snapshots have not been deleted.

## Before starting

- The damaged original host does not need to come back online.
- The source node, repository, and historical snapshots must not be deleted before recovery is verified.
- The original source node must have at least one recoverable snapshot. The **Restore** button is available regardless of whether the node is online or offline.
- A replacement host is provisioned with the operating system installed and disk space available.
- The HyperFileLens Agent is [installed](/docs/deployment/agent) on the new host and the host is online in the console.
- The new host receives a new node identity and does not automatically inherit the original backup configuration.

## Recover to a new host

### 1. Verify that snapshots are still available

1. Open **Protection → Start Backup** in the console.
2. Locate the original source node in the source list. Its status shows **Offline**.
3. Confirm the node still appears and its historical backups and snapshots are listed.

![Start Backup page showing an Offline source node with its backup entries and snapshot points still visible](/docs/en/backup-restore/recover-new-host-verify-snapshots.png)

Open the source node detail to confirm that the historical snapshot points are still available:

![Snapshot detail drawer confirming historical snapshot points are available for the offline source node](/docs/en/backup-restore/recover-new-host-verify-snapshots-detail.png)

Do not delete the offline source node, its repository, or its snapshots until recovery is complete and verified.

### 2. Add the new host and create a backup configuration

1. On the replacement host, install the HyperFileLens Agent.
2. Register the Agent to the same console. The new host appears as a separate **Online** node with a different identity.
3. Add the new host as a backup source and create a backup configuration for it. Follow [Create and run backups](/docs/backup-restore/create-backup) through the configuration steps — select the backup scope, assign a target repository, and save the configuration.
4. **Do not run the backup yet.** The restore target list only shows hosts that have a backup configuration. Saving the configuration is enough to make the new host appear as a selectable restore target.

![Start Backup page showing both the offline original node and the new online replacement node](/docs/en/backup-restore/recover-new-host-both-nodes.png)

### 3. Restore snapshots to the new host

The **Restore** button is available as long as the offline source node has recoverable snapshots. The new host now appears in the restore target list because its backup configuration exists.

1. On **Start Backup**, select the offline original source node and choose **Restore**.
2. Select **Create New Restore Task**. Do not use **Run Restore Plan** — the plan may reference the original host as the destination.
3. Under **Backups & Snapshots**, select the backup and the desired snapshot point.
4. Under **Restore Targets**, select the new replacement host as the destination node.
5. Select the snapshot scope — the entire snapshot, a directory, or individual files.
6. Choose a destination directory on the new host.
7. Set the conflict policy:
   - **Skip** — safe for a first validation pass.
   - **Overwrite** — use only when you are certain about the target contents.
8. On **Review**, verify the snapshot, destination node, restore scope, destination paths, and conflict policy, then select **Start Restore**.

![Create Restore Task with the offline original node as the snapshot source and the new online node as the restore target](/docs/en/backup-restore/recover-new-host-create-restore.png)

### 4. Verify the restored files

1. After the restore task finishes, confirm that **Restore Task** is **Succeeded** on **Start Backup**.
2. Open **Restore Records** in the original source node details and check the record status, restored count, and destination path.
3. On the new host, inspect the restored files in the destination directory. Open a sample file and confirm its content matches expectations.

![Restore Records showing a successful restore from the offline source node to the new host node](/docs/en/backup-restore/recover-new-host-restore-records.png)

Do not delete the original node, repository, or snapshots until you have confirmed that all required files are intact on the new host.

### 5. Run the first backup for the new host

The new host already has a backup configuration from step 2, but no backup has been run yet. Now that recovery is verified:

1. On **Start Backup**, select the new host and choose **Backup Now**.
2. Wait for **Backup Task** to become **Succeeded**. See [Create and run backups](/docs/backup-restore/create-backup) if you need a detailed walkthrough.
3. Configure a [backup policy and retention](/docs/backup-restore/policies) suitable for the new host.
4. Optionally configure a restore plan for the new host once the destination is established.

## Clean up the original node (optional)

After recovery is verified and the replacement host is producing its own snapshots, you may remove the offline original source node if it is no longer needed. Consult your data retention requirements before deleting the original node, repository, or its historical snapshots.

## Related pages

- [Restore files and directories](/docs/backup-restore/restore)
- [Create and run backups](/docs/backup-restore/create-backup)
- [Deploy an Agent](/docs/deployment/agent)
- [Policies and retention](/docs/backup-restore/policies)