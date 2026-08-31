---
title: 备份与恢复
description: 管理备份源、目标存储、备份配置、任务、快照和数据恢复。
---

# 备份与恢复

HyperFileLens 的数据保护流程从可读取的备份源开始，经由备份配置把数据写入目标存储并生成快照，最后通过实际恢复确认数据可用。任务成功只表示执行已经完成；快照内容和恢复结果才说明预期数据能否找回。

## 日常使用流程

1. [管理备份源](/zh/docs/backup-restore/sources)，让 Agent 或 Proxy 在线并确认目录可浏览。
2. [管理目标存储](/zh/docs/backup-restore/targets)，创建专用仓库并通过连接验证。
3. [创建并运行备份](/zh/docs/backup-restore/create-backup)，选择范围、仓库和运行选项。
4. [查看任务与快照](/zh/docs/backup-restore/snapshots)，核对任务结果和实际文件。
5. [恢复文件和目录](/zh/docs/backup-restore/restore)，先恢复到独立目录并检查内容。
6. 验证基本链路后，根据恢复点目标设置[策略与保留](/zh/docs/backup-restore/policies)。

## 三个关键判断

- **源端可读**：Agent 或 Proxy 在线，所选目录存在且具备读取权限。
- **目标可写**：对象存储、NAS 或本地磁盘验证通过，并使用明确且专用的仓库位置。
- **快照可恢复**：任务完成，快照包含预期目录，并通过实际恢复验证。

部分成功不代表全部数据已经受到保护。必须查看失败目录、跳过项和实际数据量，再判断快照是否满足恢复要求。

![首次备份完成，Backup Task 显示 Succeeded，账户、主机和仓库信息已经模糊处理](/docs/getting-started/backup-succeeded.png)

## 建议的首次验证范围

使用少量合成测试文件完成第一次端到端验证。不要一开始就选择整个系统盘、生产共享或大容量目录。首次验证应满足：

- 备份源和目标仓库均为 **Online**；
- **Backup Task** 为 **Succeeded**；
- 快照状态为 **Available**，且能浏览到预期文件；
- 单个测试文件可以恢复到独立目录，**Restore Task** 为 **Succeeded**；
- 目标端文件可以打开，内容与源文件一致。
