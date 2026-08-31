---
title: 添加备份源
description: 将本次示例使用的 Windows 主机接入 HyperFileLens。
---

# 添加备份源

本章以 Windows 主机作为备份源。此步骤只负责安装 Agent、完成注册并确认主机在线；需要保护的 `C:\HFL-Quickstart` 目录将在下一步选择。

本指南使用 `C:\HFL-Quickstart` 及其中的示例文件演示完整流程。实际使用时，可以替换为需要保护的目录和文件，并根据所选内容调整恢复验证方式和 Insights 问题；无需创建与本指南完全相同的测试数据。

## 开始前

- 已登录 HyperFileLens，并已将产品界面切换为 **English**。
- 当前账户拥有添加备份源的权限。
- Windows 主机在线并可以运行 PowerShell。
- 需要保护的目录已经准备完成；本指南中的示例目录为 `C:\HFL-Quickstart`。

## 接入 Windows 主机

1. 打开 **Protection → Backup Wizard**。
2. 确认当前处于 **Backup Sources** 步骤，然后选择 **Add Source**。
3. 选择 **Source Host**。
4. 在 **Select Target Operating System** 中选择 **Windows**。

页面会在 **Run the Install Command** 区域显示 Windows 安装命令。

![添加 Windows 备份源，安装命令中的注册信息已经遮盖](/docs/getting-started/add-windows-source.png)

## 安装 Windows Agent

1. 在安装命令旁选择 **Click to copy**。
2. 在 Windows 主机上按 **Win + R**。
3. 输入 `powershell` 并按回车。
4. 将复制的安装命令粘贴到 PowerShell，然后按回车运行。
5. 等待安装程序完成，并确认输出包含 `Installation completed successfully` 和 `Node is online in HyperFileLens`。

部署命令可能包含短时有效的注册信息。不要复用旧截图中的命令，也不要在文档、聊天或公开 Issue 中分享完整命令。

![Windows Agent 安装成功，用户路径和安装详情已经遮盖](/docs/getting-started/windows-agent-installed.png)

## 确认主机在线

返回 **Protection → Backup Wizard**，选择源端表格上方的刷新按钮，然后确认新源端显示：

- 类型为 **Host · Windows**；
- **Lifecycle Status** 为 **Registered**；
- **Connectivity** 为 **Online**。

![Windows 备份源已经注册并在线，主机名、IP 地址、账户和注册时间已经遮盖](/docs/getting-started/windows-source-online.png)

## 完成标准

- PowerShell 显示安装成功并且节点已经上线。
- Windows 主机出现在 **Backup Sources** 表格中。
- **Lifecycle Status** 为 **Registered**。
- **Connectivity** 为 **Online**。

下一步：[配置备份源](/zh/docs/getting-started/configure-source)。
