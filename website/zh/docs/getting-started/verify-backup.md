---
title: 检查任务与快照
description: 检查首次备份任务状态、快照状态和快照中的文件内容。
---

# 检查任务与快照

任务成功表示备份执行完成；快照内容检查则用于确认预期文件已经进入备份数据。两者必须结合检查。

## 检查任务

在 **Start Backup** 页面确认 **Backup Task** 显示 **Succeeded**，并核对备份源、备份路径和目标仓库。

## 打开 Snapshot Points

1. 在备份源列表中打开刚才的 Windows 备份源详情。
2. 选择 **Snapshot Points** 标签页。
3. 找到刚才生成的快照。
4. 确认快照 **Status** 为 **Available**。
5. 展开快照，检查 **Size**、**Restore Size**、**Files/Dirs** 等摘要信息。

![Snapshot Points 中的可用快照及大小、文件数量摘要，主机信息已经模糊处理，快照标识和时间保持可见](/docs/getting-started/snapshot-points-available.png)

本次测试快照的源路径应为 `C:\HFL-Quickstart`，并应包含 2 个文件：`restore-check.txt` 和 `insights\device-inventory.csv`。

## 浏览或下载快照内容

展开快照对应的源路径后：

- 选择 **Browse**，打开 **File and Directory Browser**，检查目录和文件名；
- 在浏览器中勾选文件后，可以选择 **Download** 将选中的快照内容下载到本地。

![File and Directory Browser 中的快照文件，主机信息已经模糊处理，快照标识、测试文件名、大小和时间保持可见](/docs/getting-started/browse-snapshot-files.png)

确认 `restore-check.txt` 和 `insights\device-inventory.csv` 均可见后，才继续恢复测试文件。下载的文件仍然来自快照，不要把下载操作当作恢复验证的替代。

## 完成标准

- Backup Task 为 **Succeeded**；
- 快照状态为 **Available**；
- 快照源路径为 `C:\HFL-Quickstart`；
- `restore-check.txt` 和 `insights\device-inventory.csv` 均可在浏览器中找到。

下一步：[恢复测试文件](/zh/docs/getting-started/first-restore)。
