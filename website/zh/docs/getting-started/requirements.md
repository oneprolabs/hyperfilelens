---
title: 部署要求
description: HyperFileLens 控制平面、Agent、Proxy 和 Data Gateway 的平台与资源要求。
---

# 部署要求

先确认各组件运行位置。控制平面、备份节点和 Data Gateway 的平台范围不同，不应使用 Agent 的平台范围推断控制平面也支持相同系统。

## 控制平面主机

| 项目 | 最低要求 | 建议配置 |
| --- | --- | --- |
| 操作系统 | Ubuntu 20.04、22.04 或 24.04 | Ubuntu 22.04 或 24.04 |
| 架构 | amd64 | amd64 |
| CPU | 2 核，适用于验证环境 | 4 核或更多 |
| 内存 | 4 GB，适用于轻量验证 | 8–16 GB |
| 安装目录可用空间 | 20 GiB | 根据镜像、日志和控制平面数据增长预留额外空间，优先使用 SSD |
| 容器运行时 | Docker Engine 24.0 以上、Compose V2 2.20 以上；离线包也可在未安装 Docker 时安装匹配版本 | 保持 Docker 服务健康并预留镜像空间 |

控制平面安装到 `/opt/hyperfilelens`，持久数据位于其 `data/` 目录。安装前应确认系统盘容量和备份策略，不要把应用目录当作目标备份仓库。

## 节点平台

| 节点 | 支持平台 |
| --- | --- |
| 源端 Agent | Linux amd64/arm64、macOS amd64/arm64、Windows amd64 |
| Proxy | 通过产品部署向导提供的受支持安装包部署；涉及 NAS 挂载和本地磁盘时优先使用 Linux |
| Data Gateway | 完整安装当前要求 Linux/Ubuntu amd64 |

Data Gateway 还需要 Docker Engine 和 Compose V2。离线安装程序可在受支持的 Ubuntu amd64 主机上安装捆绑的 Docker 包；如果主机已有不完整或不兼容的 Docker，安装会安全失败，而不会覆盖主机现状。

## 网络与名称解析

至少确认以下链路：

- 浏览器能够通过 HTTPS 访问 HyperFileLens 租户控制台。
- Agent、Proxy 和 Data Gateway 能够访问控制平面的注册与长连接端点。
- 源端节点或 Proxy 能够访问目标对象存储、NAS 或 Repository Server 地址。
- 使用对象存储时，主机时间准确，DNS、TLS、区域和访问策略配置正确。
- 使用 NAS 时，所需的 SMB/NFS 端口、挂载工具和字符集模块可用。

不要为排障长期关闭 TLS 验证或扩大网络访问范围。应优先修正证书信任、DNS、路由和最小必要的防火墙规则。

## 浏览器与账户

使用受支持的现代浏览器访问控制台。首次登录使用安装程序输出的初始账户；首次进入后应立即修改密码，并妥善保护安装目录中的 `.env`、证书和运行数据。
