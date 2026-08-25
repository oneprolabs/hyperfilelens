---
title: 备份与恢复
description: 管理备份源、目标存储、备份配置、任务、快照和数据恢复。
---

# 备份与恢复

HyperFileLens 的数据保护流程从备份源开始，经由备份配置把数据写入目标存储并生成快照，最后通过恢复验证确认数据真正可用。

## 日常使用流程

1. [管理备份源](/zh/docs/backup-restore/sources)，确认主机或 NAS 数据可以读取。
2. [管理目标存储](/zh/docs/backup-restore/targets)，确认仓库位置可写。
3. [创建并运行备份](/zh/docs/backup-restore/create-backup)。
4. [查看任务与快照](/zh/docs/backup-restore/snapshots)。
5. [恢复文件和目录](/zh/docs/backup-restore/restore)。
6. 根据业务要求设置[策略与保留](/zh/docs/backup-restore/policies)，并按需配置恢复计划。

## 三个关键判断

- **源端可读**：Agent 或 Proxy 在线，所选目录存在且具备读取权限。
- **目标可写**：对象存储、NAS 或本地磁盘验证通过，并使用明确且专用的仓库位置。
- **快照可恢复**：任务完成，快照包含预期目录，并通过实际恢复验证。

部分成功不代表全部数据已经受到保护。必须查看失败目录、跳过项和实际数据量，再判断快照是否满足恢复要求。
