---
title: 支持范围
description: HyperFileLens Community 当前支持的控制平面、节点、备份源和目标存储范围。
---

# 支持范围

本页总结当前代码和 Release 构建契约。具体版本的发布资产和发布说明优先于本文档；准备部署前应再次核对目标 Release。

## 运行平台

| 组件 | 操作系统 | 架构 |
| --- | --- | --- |
| HyperFileLens 控制平面 | Ubuntu 20.04、22.04、24.04 | amd64 |
| Agent | Linux | amd64、arm64 |
| Agent | macOS | amd64、arm64 |
| Agent | Windows | amd64 |
| 完整 Data Gateway | Linux/Ubuntu | amd64 |

Proxy 使用产品提供的节点安装流程。涉及 NAS 挂载、本地磁盘仓库和完整 Data Gateway 时，应使用满足向导要求的 Linux 主机。

## 备份源

- 安装 Agent 的 Linux、Windows 和 macOS 主机文件。
- 由 Proxy 访问的 NAS 共享。
- 在产品明确允许的条件下由源端直接访问的 NAS。

不应把数据库一致性、虚拟机应用一致性或特定业务系统热备能力视为文件级备份的默认保证。需要应用一致性时，应在业务侧先生成可备份的一致副本。

## 目标存储

| 类型 | 当前范围 |
| --- | --- |
| 对象存储 | AWS S3、阿里云 OSS、华为云 OBS、受支持的通用 S3 兼容服务 |
| NAS | 绑定 Proxy 的共享存储；受支持 Linux 源端可在条件满足时直接访问 |
| 本地磁盘 | Proxy 主机上的专用目录 |

自定义 S3 服务仍需满足产品使用的 S3 API、TLS、URL 方式和权限要求。“S3 兼容”不代表所有厂商行为都已验证。

## 智能洞察

Insights 仅从已有备份配置和可用快照中选择数据。创建会话需要默认 Agent 模型和在线 Data Gateway；图片理解还需要可用多模态模型。

支持的文件理解能力取决于当前 AI 引擎、模型和文件状态。加密、损坏、超大或受策略排除的文件可能无法处理。

