---
title: 配置备份源
description: 在备份向导中选择 Windows 主机和需要保护的测试目录。
---

# 配置备份源

本步骤在 **备份向导** 中选择刚注册的 Windows 主机，并将 `C:\HFL-Quickstart` 添加到备份范围。不要选择整个系统盘。

## 进入备份配置

1. 在 **备份源** 表格中勾选刚注册的 Windows 主机。
2. 选择 **下一步**，进入 **备份配置**。
3. 再次勾选该 Windows 主机，然后选择 **备份设置**。

![备份配置步骤中的 Windows 源端列表](/docs/zh/getting-started/select-source-for-setup.png)

## 选择备份内容

1. 在 **备份源** 步骤展开 Windows 备份源。
2. 在 **浏览文件和文件夹** 中找到并勾选 `C:\HFL-Quickstart`。
3. 选择 **添加已选**。
4. 确认 `C:\HFL-Quickstart` 出现在右侧 **已选路径** 中。
5. 选择 **下一步**。

![备份内容选择页面中已选的 C:\HFL-Quickstart 路径](/docs/zh/getting-started/select-backup-directory.png)

## 选择备份策略和文件过滤器

本次测试需要备份 `C:\HFL-Quickstart` 中的全部内容，因此不添加备份策略和文件过滤器，保留当前压缩设置并选择 **下一步**。

如果需要定期运行备份，可以通过 **备份策略** 选择或创建策略。如果需要排除不应进入快照的文件，可以通过 **文件过滤器** 选择或创建过滤规则。策略和过滤规则会改变后续备份行为，不要在首次验证中添加尚未确认的规则。

![备份策略和文件过滤器选择页面](/docs/zh/getting-started/optional-backup-policy.png)

## 完成标准

- 已选中正确的 Windows 主机。
- 备份范围只包含本次准备的测试目录。
- `C:\HFL-Quickstart` 显示在 **已选路径** 中。
- 本次未添加会排除测试文件的过滤规则。

如果目标端步骤中没有可用存储仓库，请不要退出向导。下一步直接在当前流程中[添加目标存储](/zh/docs/getting-started/add-target)。
