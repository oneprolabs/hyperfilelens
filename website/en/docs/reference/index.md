---
title: Core Concepts
description: Understand organizations, backup sources, target storage, jobs, snapshots, and Data Gateways in HyperFileLens.
---

# Core Concepts

These concepts explain how product pages and jobs relate to one another. For step-by-step actions, use the relevant product guide.

## Accounts and organizations

| Concept | Description |
| --- | --- |
| Organization | A workspace that contains users, backup resources, jobs, and business data |
| Member | A user who can enter an organization and use its authorized features |
| Role | A set of permissions that determines which organization actions a member can view or perform |

## Data protection

| Concept | Description |
| --- | --- |
| Backup source | Host files or NAS data that needs protection |
| Target storage | The location of a backup repository, including object storage, NAS, or local storage attached to a Proxy |
| Backup configuration | The backup scope, target storage, policy, and optional restore plan used together |
| Backup policy | Reusable scheduling, file-filtering, retention, and transfer rules |
| Job | The execution record for a backup, restore, or storage-maintenance operation |
| Snapshot | A browsable point in time created by a backup job |
| Restore job | One execution that restores files or folders from a selected snapshot to a destination host |
| Restore plan | A predefined scope, destination, and file-conflict behavior that runs from the latest available snapshot |

A backup configuration defines how protection runs. A job records each execution. A snapshot holds the point-in-time data that can be browsed and restored.

## Nodes

- An **Agent** runs on a protected host, accesses local files, and performs backup and restore jobs.
- A **Proxy** connects NAS or local storage so it can be used as a backup source or target.
- A **Public Data Gateway** is platform-provided and is the default way to prepare data for Insights.
- A **Private Data Gateway** runs on a user-managed network to reach repositories that the Public Data Gateway cannot access or to process data in that network.

An online component is connected to the control plane. Whether it can reach a backup source or target storage must still be confirmed by connection validation and actual jobs.

## Insights

- An **insight session** is associated with a backup source, a specific snapshot, a selected data scope, and a Data Gateway.
- The **data scope** contains the files and folders explicitly selected from a snapshot for that session.
- A **citation** locates source material used by an answer; it does not mean the conclusion has been independently verified.

Insights works with backup snapshots, not live files on a protected host. After production files change, create a new snapshot before analyzing the updated data.
