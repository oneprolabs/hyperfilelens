---
title: 创建并运行首次备份
description: 分配目标仓库、确认备份配置并运行首次备份。
---

# 创建并运行首次备份

本次使用已创建的 Huawei Cloud OBS 仓库，备份 `C:\HFL-Quickstart` 中的全部内容。先完成小范围闭环，再扩大到实际需要保护的数据。

## 分配目标仓库

1. 返回 **Target** 步骤，选择源端行右侧的小铅笔图标。
2. 在 **Select Target Repository** 中选择刚创建的 Huawei Cloud 仓库。
3. 选择 **OK**。
4. 确认目标列显示仓库、**Object Storage** 和 **Online**。

![在 Select Target Repository 中选择已创建且在线的 Huawei Cloud 仓库，账户和仓库名称已经模糊处理](/docs/getting-started/select-target-repository.png)

![目标仓库已分配给 Windows 备份源，账户、主机和仓库名称已经模糊处理，公开 Endpoint 保持可见](/docs/getting-started/assigned-target-repository.png)

## 可选的恢复计划

选择 **Next** 进入 **Restore Plan**。恢复计划是可选配置，用于预设恢复范围、目标节点、目标目录，以及同名文件的跳过或覆盖策略。

需要以后按相同规则重复恢复时，可以启用恢复计划。临时恢复、恢复历史版本或临时改变目标位置时，使用手动恢复更合适。首次备份可以不配置恢复计划，直接选择 **Next**。

![可选的 Restore Plan 配置，恢复目标主机已经模糊处理，恢复路径和冲突策略保持可见](/docs/getting-started/optional-restore-plan.png)

## Review 并创建配置

1. 在 **Review** 页面核对备份源、`C:\HFL-Quickstart`、目标仓库、压缩设置、策略、过滤器和恢复计划。
2. 确认信息正确后选择 **Create**。

![Review 页面中的备份配置摘要，账户、主机和仓库名称已经模糊处理，恢复路径和配置标签保持可见](/docs/getting-started/review-backup-configuration.png)

创建完成后，页面返回 **Start Backup** 步骤。表格会列出备份路径、目标仓库、连接状态和任务状态。

## 运行首次备份

1. 在 **Start Backup** 页面确认目标仓库的 **Connectivity** 为 **Online**。
2. 勾选当前 Windows 备份源。
3. 选择 **Backup Now**。
4. 在 **Backup Task** 列观察任务状态和进度。
5. 等待状态变为 **Succeeded**。

![备份配置已准备运行，Connectivity 为 Online，账户和主机信息已经模糊处理](/docs/getting-started/backup-ready-to-run.png)

![首次备份完成，Backup Task 显示 Succeeded，账户、主机和仓库信息已经模糊处理](/docs/getting-started/backup-succeeded.png)

运行期间不要关闭 Windows Agent，也不要修改对象存储凭据。只有当 **Backup Task** 显示 **Succeeded** 后，才继续检查任务与快照。

下一步：[检查任务与快照](/zh/docs/getting-started/verify-backup)。
