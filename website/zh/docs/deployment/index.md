---
title: 部署与运维
description: 安装和维护 HyperFileLens Community，以及管理 Agent、Proxy 和 Private Data Gateway。
---

# 部署与运维

本节主要面向 Community 自托管用户。官方 SaaS 的控制平面由官方维护，SaaS 用户只需按业务需要部署 Agent、Proxy 或 Private Data Gateway。

## Community 部署

1. 查看[系统要求](/zh/docs/deployment/requirements)。
2. 使用当前公开方式[安装 Community](/zh/docs/getting-started/install)。
3. 完成[安装后检查](/zh/docs/deployment/post-install)。
4. 根据实际环境配置[网络与端口](/zh/docs/deployment/network)。

## 组件部署

- [Agent 与 Proxy](/zh/docs/deployment/nodes)：保护主机文件、连接 NAS 或提供本地目标存储。
- [Private Data Gateway](/zh/docs/deployment/data-gateway)：在可控网络中准备智能洞察所选的备份数据。

## 系统维护

- 使用受支持的安装程序完成[升级、备份与回退](/zh/docs/deployment/lifecycle)。
- 在[任务、告警与日志](/zh/docs/deployment/operations)中检查日常运行状态。
