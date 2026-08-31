---
title: 安装社区版
description: 在自有 Ubuntu 主机上安装并运行 HyperFileLens 社区版。
---

# 安装社区版

HyperFileLens 社区版可部署在自有 Ubuntu 主机上。在线安装程序会下载并启动最新发布版本。

## 安装前准备

- Ubuntu 20.04、22.04 或 24.04，amd64 架构。
- 至少 2 核 CPU 和 4 GiB 内存，建议使用 4 核 CPU 和 8 GiB 以上内存。
- `/opt` 所在磁盘至少有 20 GiB 可用空间。
- 已安装并启动 Docker Engine 和 Docker Compose V2。
- 已安装 `curl`，并具备 `sudo` 权限。
- 能够访问 Gitee、镜像仓库和 Ubuntu 软件源。
- 默认服务端口 `11442–11445` 未被其他程序占用。

详细配置和网络要求请查看[系统要求](/zh/docs/deployment/requirements)与[网络和端口](/zh/docs/deployment/network)。

## 执行安装

在准备好的主机上运行以下命令：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn
```

安装程序会显示即将安装的版本和下载来源。确认信息无误后继续，等待安装和服务启动完成。

### 安装指定版本（可选）

如需安装已经发布的指定版本：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn --tag vX.Y.Z
```

将 `vX.Y.Z` 替换为实际版本号。

## 检查安装结果

运行以下命令确认服务状态：

```bash
sudo /opt/hyperfilelens/install.sh status
```

确认核心服务处于运行或健康状态。安装异常时，请查看[安装与节点](/zh/docs/troubleshooting/installation-nodes)。

安装程序完成后，命令行会列出访问地址。请将其中标记为 `Tenant` 的完整地址复制到浏览器中，打开 HyperFileLens 控制台。其他地址用于网站访问或系统管理，首次使用无需访问。

使用安装程序提供的初始账户登录后，请立即修改初始密码。
