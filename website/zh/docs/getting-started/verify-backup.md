---
title: 检查任务与快照
description: 检查首次备份任务状态、快照状态和快照中的文件内容。
---

# 检查任务与快照

任务成功表示备份执行完成；快照内容检查则用于确认预期文件已经进入备份数据。两者必须结合检查。

## 检查任务

在 **开始备份** 页面确认 **备份任务** 显示 **成功**，并核对备份源、备份路径和目标仓库。

## 打开快照时间点

1. 在备份源列表中打开刚才的 Windows 备份源详情。
2. 选择 **快照时间点** 标签页。
3. 找到刚才生成的快照。
4. 确认快照 **状态** 为 **可用**。
5. 展开快照，检查 **大小**、**恢复大小**、**文件/目录数** 等摘要信息。

![Snapshot Points 中的可用快照及大小、文件数量摘要，主机信息已经模糊处理，快照标识和时间保持可见](/docs/zh/getting-started/snapshot-points-available.png)

本次测试快照的源路径应为 `C:\HFL-Quickstart`，并应包含 2 个文件：`restore-check.txt` 和 `insights\device-inventory.csv`。

## 浏览或下载快照内容

展开快照对应的源路径后：

- 选择 **浏览文件**，打开 **文件和目录浏览器**，检查目录和文件名；
- 在浏览器中勾选文件后，可以选择 **下载** 将选中的快照内容下载到本地。

![File and Directory Browser 中的快照文件，主机信息已经模糊处理，快照标识、测试文件名、大小和时间保持可见](/docs/zh/getting-started/browse-snapshot-files.png)

确认 `restore-check.txt` 和 `insights\device-inventory.csv` 均可见后，才继续恢复测试文件。下载的文件仍然来自快照，不要把下载操作当作恢复验证的替代。

## 完成标准

- 备份任务为 **成功**；
- 快照状态为 **可用**；
- 快照源路径为 `C:\HFL-Quickstart`；
- `restore-check.txt` 和 `insights\device-inventory.csv` 均可在浏览器中找到。
