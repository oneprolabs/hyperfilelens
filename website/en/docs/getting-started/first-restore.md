---
title: Restore a test file
description: Restore restore-check.txt from the first snapshot and verify the result.
---

# Restore a test file

This step restores `restore-check.txt` from the verified snapshot to the independent directory `C:\HFL-Restore-Test`. Do not restore it to the source directory or overwrite the original file.

## Choose a restore mode

1. Return to **Start Backup** and select the Windows backup source.
2. Select **Restore**.
3. On **Create Restore Task**, choose a mode:
   - **Run Restore Plan** immediately uses the latest snapshot, scope, destination, and conflict policy preset in the backup configuration.
   - **Create New Restore Task** lets you select the snapshot, file, and destination manually.
4. Select **Create New Restore Task** for this single-file verification.

![Two restore modes in Create Restore Task with host and IP information blurred while snapshot time, size, restore path, and policy remain visible](/docs/getting-started/choose-restore-mode.png)

## Create the single-file restore task

1. Under **Backups & Snapshots**, select the snapshot you verified, then select **Next**.

![Select the snapshot to restore with the host and IP address blurred while the snapshot time and size remain visible](/docs/getting-started/select-restore-snapshot.png)

2. Under **Restore Targets**, select the online Windows destination. This run uses **Restore to Source**, but restores into a separate test directory.
3. Select **Next**.

![Select the Windows restore target with the host and IP address blurred while the snapshot time and Restore to Source option remain visible](/docs/getting-started/select-restore-target.png)

4. Under **Restore Directories**, set **File conflict policy** to **Skip**.
5. Set **Restore Scope** to `C:\HFL-Quickstart\restore-check.txt`.
6. Set **Restore Directory** to `C:\HFL-Restore-Test`.
7. Confirm that **Restored path** is `C:\HFL-Restore-Test\restore-check.txt`, then select **Next**.

![Map restore-check.txt to the independent restore directory using Skip with host and IP details blurred](/docs/getting-started/map-restore-file.png)

## Review and run the restore

On **Review**, confirm:

- the correct snapshot is selected;
- the restore scope contains only `restore-check.txt`;
- the correct Windows target is selected;
- the restored path is `C:\HFL-Restore-Test\restore-check.txt`; and
- the conflict policy is **Skip duplicate files (keep source)**.

Select **Start Restore**.

![Single-file restore task Review with the host and IP address blurred while the snapshot time, restore path, and conflict policy remain visible](/docs/getting-started/review-restore-task.png)

After returning to **Start Backup**, monitor **Restore Task** and wait for the status to become **Succeeded**.

![Completed restore with Restore Task showing Succeeded and account, host, and repository details blurred](/docs/getting-started/restore-succeeded.png)

You can also open the backup source details and select **Restore Records**. Confirm that the record and file item are both **Succeeded** and that one file was restored.

![Successful single-file restore in Restore Records with host and IP information blurred while Record, Task, and Snapshot identifiers and times remain visible](/docs/getting-started/restore-record-succeeded.png)

## Verify the file on Windows

A **Succeeded** console status only confirms that the task finished successfully. Verify the actual file on Windows:

1. Open `C:\HFL-Restore-Test\restore-check.txt`.
2. Confirm that it contains:

   ```text
   HyperFileLens restore verification
   Verification code: HFL-810-RESTORE-742
   ```

3. Calculate the restored file's SHA-256 in PowerShell:

   ```powershell
   Get-FileHash "C:\HFL-Restore-Test\restore-check.txt" -Algorithm SHA256
   ```

4. Confirm that the result matches the source baseline:

   ```text
   C697CF93D9D0C475F8732B99F6C4690B9B064B6774B4054C198F859AF5E35C2D
   ```

You can also open the source and restore directories side by side and compare the file name, size, and content. In this verification, `restore-check.txt` has identical content before and after restore.

![Matching restore-check.txt content in the source and restore directories](/docs/getting-started/restore-content-verified.png)

The first backup is proven recoverable after the restored content has been checked on Windows; matching the SHA-256 baseline provides the stronger verification.

Next: [Create an Insights session](/en/docs/getting-started/first-insight) from the same backup snapshot.
