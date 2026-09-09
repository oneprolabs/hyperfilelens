---
title: 安装社区版
description: 在自有 Ubuntu 主机上安装并运行 HyperFileLens 社区版。
---

# 安装社区版

HyperFileLens 社区版可部署在自有 Ubuntu 主机上。在线安装程序会下载并启动最新发布版本。

## 安装前准备

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Ubuntu 20.04、22.04 或 24.04，amd64 架构 |
| CPU 与内存 | 最低 4 核 CPU、8 GiB 内存；建议 8 核 CPU、16 GiB 以上内存 |
| 磁盘空间 | `/opt` 所在磁盘至少有 20 GiB 可用空间 |
| 容器运行环境 | Docker Engine 24.0.0 及以上版本、Docker Compose V2 2.20.0 及以上版本，且 Docker daemon 正常运行 |
| 基础工具 | 已安装 `curl` 和 Python 3，并具备 `sudo` 权限 |
| 网络访问 | 能够访问 Gitee、镜像仓库、Docker CE 软件源和 Ubuntu 软件源 |
| 服务端口 | `11442–11445/TCP` 未被其他程序占用 |

::: info Docker 环境说明

- **安装与复用：** 主机未安装 Docker 时，安装程序可自动安装 Docker CE 和 Compose V2；已有环境符合要求时直接复用。仅缺少 Compose V2 时，只在不改变现有 Docker 软件包的前提下自动补装。
- **兼容范围：** 仅支持 Docker CE，不支持 Ubuntu `docker.io`、Moby、Snap 或无法识别的容器运行环境。安装程序不会替换或修复已有 Docker，不符合要求时需先手动处理。
- **卸载范围：** 卸载 HyperFileLens 不会移除 Docker、Compose、containerd，也不会删除安装程序配置的 Docker CE 软件源。

:::

详细配置和网络要求请查看[系统要求](/zh/docs/deployment/requirements)与[网络和端口](/zh/docs/deployment/network)。

## 执行安装

在准备好的主机上运行以下命令：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn --yes
```

安装程序会显示即将安装的版本和下载来源，随后自动继续。请等待安装和服务启动完成。

## 检查安装结果

运行以下命令确认服务状态：

```bash
sudo /opt/hyperfilelens/install.sh status
```

确认核心服务处于运行或健康状态。安装异常时，请查看[安装与节点](/zh/docs/troubleshooting/installation-nodes)。

安装成功后，`Access` 区域会显示以下入口：

| 入口 | 用途 |
| --- | --- |
| `HyperFileLens` | 产品控制台，用于备份、恢复、智能洞察和组织内管理 |
| `Platform Ops` | 平台管理控制台，用于 AI 模型、系统配置和平台运维 |

首次使用时，在 `HyperFileLens` 下复制 `URL` 并通过浏览器打开，然后使用同一区域显示的 `Email` 和 `Password` 登录。首次登录后请立即修改初始密码。备份、恢复和智能洞察在产品控制台中完成；配置 AI 模型或执行平台管理操作时，才需进入 `Platform Ops`。
