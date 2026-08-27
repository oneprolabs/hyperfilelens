---
title: 部署 Agent
description: 在需要保护的主机上安装、注册和检查 HyperFileLens Agent。
---

# 部署 Agent

Agent 安装在需要保护的 Windows、Linux 或 macOS 主机上，用于访问源端文件并执行备份与恢复任务。

## 部署前准备

- 确认主机平台和基础资源符合[系统要求](/zh/docs/deployment/requirements)。
- 确认主机能够连接 HyperFileLens 控制平面和计划使用的目标存储，所需连接请查看[网络与端口](/zh/docs/deployment/network)。
- 根据需要保护的文件范围，准备相应的用户或管理员权限。安装程序会根据运行命令的身份自动确定 Agent 的运行方式。

## 安装 Agent

1. 打开<span class="hfl-path">数据保护 → 源端资源</span>。
2. 选择<span class="hfl-ui">添加源端主机</span>，进入部署向导。
3. 选择目标主机的操作系统。
4. 生成安装命令，并按照向导提示，以希望保护文件的用户身份在目标主机上运行。需要持续保护主机范围文件时，使用管理员权限运行。
5. 等待安装完成，返回源端资源页面确认主机已经注册。

## 验证部署

在控制台确认：

- 源端主机状态为在线。
- 可以浏览所需目录，并选择预期的备份路径。
- 对于持续运行的保护方式，主机或 Agent 服务重启后能够自动重新连接。

注册失败或 Agent 离线时，进入[安装与节点](/zh/docs/troubleshooting/installation-nodes)。
