---
title: Product Workflow
description: Understand how backups, snapshots, restores, and AI-powered insights work together in HyperFileLens.
---

# Product Workflow

HyperFileLens is built around backup snapshots. A backup configuration connects the data to protect, its target storage, and the policy that controls execution. Once a backup job creates a snapshot, you can restore files and folders or select snapshot data for AI-powered analysis.

## End-to-end flow

**Backup source and target storage → Backup configuration → Backup job → Snapshot → Restore or Insights**

A snapshot is both the result of a backup and the shared data foundation for restores and Insights. Run restore tests on a schedule that matches your recovery requirements to confirm that the expected data can be recovered.

## Backup and restore

The data protection lifecycle is straightforward:

1. Add a [backup source](/en/docs/backup-restore/sources) and [target storage](/en/docs/backup-restore/targets), then validate access to both.
2. [Create and run a backup](/en/docs/backup-restore/create-backup), adding [policies and retention](/en/docs/backup-restore/policies) as needed.
3. [View tasks and snapshots](/en/docs/backup-restore/snapshots) to confirm that the expected files are present.
4. [Restore files and directories](/en/docs/backup-restore/restore) to prove that the protected data is usable.

See the [Backup and Restore workflow](/en/docs/backup-restore/) for the complete guide.

## Insights

Insights works with existing backup snapshots and does not read live files from a protected host:

1. Select a backup configuration, a specific snapshot, and the files or folders to analyze.
2. Use the default Public Data Gateway, or select a Private Data Gateway that can reach the repository.
3. Create an insight session and wait for the selected data to be prepared.
4. Use citations to understand the evidence and source material behind an answer.

## First-time use

If you are new to HyperFileLens, follow the [Quick Start](/en/docs/) and use the same test data to complete a backup, restore, and insight session. Once that workflow succeeds, configure schedules, retention, and routine validation for your actual data and recovery requirements.
