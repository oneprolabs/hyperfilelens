---
title: Check tasks and snapshots
description: Verify the first backup task, snapshot status, and snapshot contents.
---

# Check tasks and snapshots

A successful task confirms that the backup execution finished. Inspecting the snapshot confirms that the expected files are actually present. Check both.

## Check the task

On **Start Backup**, confirm that **Backup Task** shows **Succeeded**, then check the backup source, backup path, and target repository.

## Open Snapshot Points

1. Open the Windows backup source details from the backup source list.
2. Select the **Snapshot Points** tab.
3. Find the snapshot created by the backup you just ran.
4. Confirm that its **Status** is **Available**.
5. Expand the snapshot and review **Size**, **Restore Size**, and **Files/Dirs**.

![Available snapshot in Snapshot Points with host information blurred while snapshot identifiers, times, sizes, and file counts remain visible](/docs/getting-started/snapshot-points-available.png)

For this test, the source path should be `C:\HFL-Quickstart` and the snapshot should contain two files: `restore-check.txt` and `insights\device-inventory.csv`.

## Browse or download snapshot contents

Expand the source path for the snapshot:

- Select **Browse** to open **File and Directory Browser** and inspect the directory and file names.
- Select files in the browser, then select **Download** to download the selected snapshot contents locally.

![Snapshot files in File and Directory Browser with host information blurred while snapshot identifiers, test filenames, sizes, and times remain visible](/docs/getting-started/browse-snapshot-files.png)

Confirm that both `restore-check.txt` and `insights\device-inventory.csv` are visible before restoring a test file. Downloaded files still come from the snapshot; downloading is not a substitute for restore verification.

## Completion criteria

- **Backup Task** is **Succeeded**.
- The snapshot **Status** is **Available**.
- The snapshot source path is `C:\HFL-Quickstart`.
- `restore-check.txt` and `insights\device-inventory.csv` are visible in the browser.

Next: [Restore a test file](/en/docs/getting-started/first-restore).
