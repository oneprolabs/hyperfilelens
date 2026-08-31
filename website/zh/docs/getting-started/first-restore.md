---
title: 恢复测试文件
description: 从首次备份快照恢复 restore-check.txt，并验证恢复结果。
---

# 恢复测试文件

本步骤从刚验证的快照中恢复 `restore-check.txt`，目标是独立目录 `C:\HFL-Restore-Test`。不要恢复回源目录，也不要覆盖原文件。

## 选择恢复方式

1. 返回 **Start Backup**，勾选当前 Windows 备份源。
2. 选择 **Restore**。
3. 在 **Create Restore Task** 中选择恢复方式：
   - **Run Restore Plan**：按备份配置中预设的最新快照、范围、目标和冲突策略立即运行；
   - **Create New Restore Task**：手动选择快照、文件和恢复目标。
4. 本次需要验证单文件恢复，因此选择 **Create New Restore Task**。

![Create Restore Task 中的两种恢复方式，主机和 IP 已经模糊处理，快照时间、大小、恢复路径和策略保持可见](/docs/getting-started/choose-restore-mode.png)

## 创建单文件恢复任务

1. 在 **Backups & Snapshots** 中选择刚才验证的快照，然后选择 **Next**。

![选择要恢复的快照，主机和 IP 已经模糊处理，快照时间和大小保持可见](/docs/getting-started/select-restore-snapshot.png)

2. 在 **Restore Targets** 中选择在线的 Windows 目标。本次选择 **Restore to Source**，但恢复目录仍使用独立测试目录。
3. 选择 **Next**。

![选择 Windows 恢复目标，主机和 IP 已经模糊处理，快照时间和 Restore to Source 选项保持可见](/docs/getting-started/select-restore-target.png)

4. 在 **Restore Directories** 中将 **File conflict policy** 设置为 **Skip**。
5. 将 **Restore Scope** 设置为 `C:\HFL-Quickstart\restore-check.txt`。
6. 将 **Restore Directory** 设置为 `C:\HFL-Restore-Test`。
7. 确认页面计算的 **Restored path** 为 `C:\HFL-Restore-Test\restore-check.txt`，然后选择 **Next**。

![将 restore-check.txt 映射到独立恢复目录并使用 Skip，主机和 IP 已经模糊处理](/docs/getting-started/map-restore-file.png)

## Review 并运行恢复

在 **Review** 中确认：

- 使用正确的快照；
- 恢复范围是单个 `restore-check.txt`；
- 恢复目标为正确的 Windows 主机；
- 恢复路径为 `C:\HFL-Restore-Test\restore-check.txt`；
- 冲突策略为 **Skip duplicate files (keep source)**。

确认后选择 **Start Restore**。

![单文件恢复任务 Review，主机和 IP 已经模糊处理，快照时间、恢复路径和冲突策略保持可见](/docs/getting-started/review-restore-task.png)

返回 **Start Backup** 后，可以在 **Restore Task** 列查看进度。等待状态变为 **Succeeded**。

![恢复任务完成，Restore Task 显示 Succeeded，账户、主机和仓库信息已经模糊处理](/docs/getting-started/restore-succeeded.png)

还可以打开备份源详情并选择 **Restore Records**，确认记录状态和文件项均为 **Succeeded**，恢复数量为 1 个文件。

![Restore Records 中的单文件恢复成功记录，主机和 IP 已经模糊处理，Record、Task、Snapshot 标识及时间保持可见](/docs/getting-started/restore-record-succeeded.png)

## 在 Windows 上验证文件

控制台显示 **Succeeded** 只说明恢复任务成功结束。还必须在 Windows 上检查实际文件：

1. 打开 `C:\HFL-Restore-Test\restore-check.txt`。
2. 确认内容包含：

   ```text
   HyperFileLens restore verification
   Verification code: HFL-810-RESTORE-742
   ```

3. 在 PowerShell 中计算恢复文件的 SHA-256：

   ```powershell
   Get-FileHash "C:\HFL-Restore-Test\restore-check.txt" -Algorithm SHA256
   ```

4. 确认结果等于源文件基准值：

   ```text
   C697CF93D9D0C475F8732B99F6C4690B9B064B6774B4054C198F859AF5E35C2D
   ```

也可以同时打开源目录和恢复目录，直接比较文件名、大小和内容。下面的验证使用 `restore-check.txt`，恢复前后的内容一致。

![恢复前后的源目录和恢复目录中的 restore-check.txt 内容一致](/docs/getting-started/restore-content-verified.png)

完成 Windows 文件内容检查；如果 SHA-256 也与基准值一致，即可确认首次备份具备实际可恢复性。

下一步：[创建洞察会话](/zh/docs/getting-started/first-insight)，继续使用同一份备份快照。
