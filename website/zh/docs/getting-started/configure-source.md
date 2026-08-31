---
title: 配置备份源
description: 在备份向导中选择 Windows 主机和需要保护的测试目录。
---

# 配置备份源

本步骤在 **Backup Wizard** 中选择刚注册的 Windows 主机，并将 `C:\HFL-Quickstart` 添加到备份范围。不要选择整个系统盘。

## 进入备份配置

1. 在 **Backup Sources** 表格中勾选刚注册的 Windows 主机。
2. 选择 **Next**，进入 **Backup Configuration**。
3. 再次勾选该 Windows 主机，然后选择 **Backup Setup**。

![在 Backup Configuration 中选择已注册的 Windows 源端，主机名、IP 和账户已经模糊处理，注册时间保持可见](/docs/getting-started/select-source-for-setup.png)

## 选择备份内容

1. 在 **Sources** 步骤展开 Windows 备份源。
2. 在 **Browse Files and Folders** 中找到并勾选 `C:\HFL-Quickstart`。
3. 选择 **Add Selected**。
4. 确认 `C:\HFL-Quickstart` 出现在右侧 **Selected Paths** 中。
5. 选择 **Next**。

![C:\HFL-Quickstart 已经添加到 Selected Paths，主机名和 IP 地址已经模糊处理](/docs/getting-started/select-backup-directory.png)

## 选择备份策略和文件过滤器

本次测试需要备份 `C:\HFL-Quickstart` 中的全部内容，因此不添加备份策略和文件过滤器，保留当前压缩设置并选择 **Next**。

如果需要定期运行备份，可以通过 **Backup Policy** 选择或创建策略。如果需要排除不应进入快照的文件，可以通过 **Filter Rule** 选择或创建过滤规则。策略和过滤规则会改变后续备份行为，不要在首次验证中添加尚未确认的规则。

![Backup Policy 和 File Filter 保持可选状态，Windows 主机名和账户已经模糊处理](/docs/getting-started/optional-backup-policy.png)

## 完成标准

- 已选中正确的 Windows 主机。
- 备份范围只包含本次准备的测试目录。
- `C:\HFL-Quickstart` 显示在 **Selected Paths** 中。
- 本次未添加会排除测试文件的过滤规则。

如果目标端步骤中没有可用存储仓库，请不要退出向导。下一步直接在当前流程中[添加目标存储](/zh/docs/getting-started/add-target)。
