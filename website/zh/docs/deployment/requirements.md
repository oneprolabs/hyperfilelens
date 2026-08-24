---
title: 系统要求
description: 安装 HyperFileLens Community 及相关节点前需要满足的平台与资源条件。
---

# 系统要求

本页说明 Community 自托管和相关节点的基础要求。官方 SaaS 用户无需部署控制平面，只需准备需要保护的主机、存储和网络。

## Community 控制平面

| 项目 | 最低要求 | 建议配置 |
| --- | --- | --- |
| 操作系统 | Ubuntu 20.04、22.04 或 24.04 | Ubuntu 22.04 或 24.04 |
| 架构 | amd64 | amd64 |
| CPU | 2 核，用于轻量验证 | 4 核或更多 |
| 内存 | 4 GB，用于轻量验证 | 8–16 GB |
| 可用空间 | 20 GiB | 根据镜像、日志和运行数据额外预留，优先使用 SSD |
| 容器运行时 | Docker Engine、Docker Compose V2 | 保持服务健康并预留镜像空间 |

控制平面安装到 `/opt/hyperfilelens`。不要把应用目录作为业务备份的目标仓库。

## 节点平台

| 节点 | 支持平台 |
| --- | --- |
| Agent | Linux amd64/arm64、macOS amd64/arm64、Windows amd64 |
| Proxy | 使用产品向导提供的受支持安装包；NAS 和本地磁盘场景优先使用 Linux |
| Private Data Gateway | Linux/Ubuntu amd64 |

具体版本是否包含相应安装介质，以该版本发布说明和产品部署向导为准。

## 网络与账户

- 浏览器能够通过 HTTPS 访问控制台。
- Agent、Proxy 和 Data Gateway 能够访问控制平面。
- 备份节点能够访问目标对象存储、NAS 或 Repository Server。
- 主机时间、DNS 和 TLS 信任正确。
- Community 安装主机具备 `sudo` 权限。

