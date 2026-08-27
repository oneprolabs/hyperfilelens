---
title: 支持范围
description: 查看 HyperFileLens Community 当前支持的运行平台、备份源、目标存储和智能洞察范围。
---

# 支持范围

本页列出 HyperFileLens Community 当前支持的主要平台和产品能力。不同版本可能存在差异，部署前请同时查看目标版本的发布说明。

## 运行平台

| 组件 | 操作系统 | 架构 |
| --- | --- | --- |
| Community 控制平面 | Ubuntu 20.04、22.04、24.04 | amd64 |
| Agent | Linux | amd64、arm64 |
| Agent | Windows | amd64 |
| Agent | macOS | amd64、arm64 |
| Proxy | Ubuntu 20.04、22.04、24.04 | amd64 |
| Private Data Gateway | Ubuntu 20.04、22.04、24.04 | amd64 |

安装所需的 CPU、内存和磁盘空间请查看[系统要求](/zh/docs/deployment/requirements)。

## 备份源

| 类型 | 当前范围 |
| --- | --- |
| 主机文件 | 安装 Agent 的 Linux、Windows 和 macOS 主机 |
| NAS | 通过 Proxy 连接的 SMB 或 NFS 共享；产品页面明确允许时，受支持的 Linux 备份主机也可直接连接 |

HyperFileLens 当前提供文件级备份。数据库、虚拟机或业务应用需要一致性副本时，应先使用相应系统提供的方式生成可备份数据。

## 目标存储

| 类型 | 当前范围 |
| --- | --- |
| 对象存储 | AWS S3、阿里云 OSS、华为云 OBS、受支持的通用 S3 兼容服务 |
| NAS | 通过 Proxy 连接的 SMB 或 NFS 共享；产品页面明确允许时，受支持的 Linux 备份主机也可直接连接 |
| 本地磁盘 | Proxy 主机上的专用目录 |

通用 S3 兼容服务需要满足产品使用的 S3 API、TLS、地址格式和权限要求。不同厂商的兼容实现可能存在差异，应在正式使用前完成连接、备份和恢复验证。

## 智能洞察

智能洞察仅使用已有备份配置中的可用快照。创建会话需要可用的默认 AI 模型和在线 Data Gateway；处理图片或扫描文档时，还需要可用的多模态模型。

支持的文件理解能力取决于当前 AI 引擎、模型和文件状态。加密、损坏、超大或受策略排除的文件可能无法处理。
