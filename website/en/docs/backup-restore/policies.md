---
title: Policies and Retention
description: Configure schedules, retention tiers, error handling, and file-filter rules.
---

# Policies and Retention

A backup policy controls when backups run and how many recovery points remain available. A file filter controls which content does not enter future snapshots. Validate the complete workflow with a manual backup before adding policies to a stable data scope.

## Backup policies

Open **Protection → Backup Policies** and select **Create Backup Policy**. The editor offers **Quick Schedule** and **Advanced Schedule** and shows a rule preview on the right. Confirm:

- whether the policy is enabled;
- the schedule time zone, start time, cycle, and interval, or the advanced Cron expression;
- retention for the latest, hourly, daily, and monthly restore points;
- handling for unreadable directories, unreadable files, and unsupported filesystem entries.

![Create Backup Policy showing quick schedule, retention, and error-handling preview with the personal account blurred](/docs/backup-restore/backup-policy-editor.png)

Choose the schedule from the recovery point objective and the time required for one backup. A frequent schedule does not resolve an offline source, an unreachable repository, or tasks that run longer than their interval.

A snapshot can qualify for more than one retention tier. Before saving, inspect **Rule Preview** and confirm that the target repository has enough capacity. Reducing retention removes older recovery opportunities; increasing it raises storage and maintenance requirements.

## File filters

Open **Protection → File Filters** and select **Create File Filter**. You can use quick presets for temporary files, development and build caches, and system junk, or add custom exclusion rules one per line. Rules are case-sensitive; do not combine multiple rules with commas or semicolons.

![Create File Filter showing quick presets and the custom exclusion editor with the personal account blurred](/docs/backup-restore/file-filter-editor.png)

Also inspect maximum file size, cache-directory behavior, and **Current Filesystem Only**. Use exclusions for confirmed temporary, cache, or reproducible content. Do not exclude an unverified data scope merely to make the first task faster.

Rule changes affect future snapshots and cannot add files that were never included in an existing snapshot. Browse the next snapshot after every change.

## Assign rules to a backup

After creating a policy or filter, return to **Backup Configuration → Backup Setup** and select it under **Backup Policy** or **File Filter**. Creating a rule alone does not change an existing backup configuration. After saving, check the next trigger time, skipped items, and snapshot contents.
