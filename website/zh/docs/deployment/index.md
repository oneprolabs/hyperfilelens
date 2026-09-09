---
title: 部署指南
description: 根据使用方式部署 HyperFileLens 控制平面、备份组件和 Private Data Gateway。
---

# 部署指南

<p class="hfl-doc-lead">社区版控制平面部署在自有 Ubuntu 主机。根据备份源、目标存储和快照数据所在的网络，按需部署 Agent、Proxy 或 Private Data Gateway。</p>

## 部署社区版

1. 查看[系统要求](/zh/docs/deployment/requirements)。
2. 根据实际环境规划[网络与端口](/zh/docs/deployment/network)。
3. 按照[安装社区版](/zh/docs/getting-started/install)完成安装。
4. 完成[安装后检查](/zh/docs/deployment/post-install)。

## 组件部署

- [部署 Agent](/zh/docs/deployment/agent)：安装在需要保护的 Windows、Linux 或 macOS 主机上，用于访问源端文件并执行备份与恢复任务。
- [部署 Proxy](/zh/docs/deployment/proxy)：部署在能够访问 NAS 或本地存储的网络中，为备份与恢复提供存储连接和数据访问能力。
- [部署 Private Data Gateway](/zh/docs/deployment/data-gateway)：当公共 Data Gateway 无法访问私有备份仓库时，将其部署在仓库可达的网络中，让智能洞察能够访问所选快照并准备分析数据。

## 运行维护

- [任务、告警与审计](/zh/docs/deployment/operations)：查看任务状态、告警信息和审计记录，完成日常检查与问题定位。
- [升级与恢复](/zh/docs/deployment/lifecycle)：升级社区版、验证升级结果，并在异常时按安全流程处理。
- [卸载社区版](/zh/docs/deployment/uninstall)：完整移除 HyperFileLens，或在保留配置和业务数据的情况下移除运行组件。
