---
title: Agent 与 Proxy
description: 使用产品部署向导安装、注册和检查 Agent 与 Proxy 节点。
---

# Agent 与 Proxy

Agent 用于保护所在主机的文件，Proxy 用于连接 NAS、提供本地磁盘仓库或承担共享存储访问。两者都通过控制台生成的一次性部署流程注册。

## 部署 Agent

1. 打开<span class="hfl-path">数据保护 → 源端资源</span>。
2. 选择添加源端主机，进入部署向导。
3. 选择 Linux、Windows 或 macOS。
4. 复制界面生成的命令，在目标主机上运行。
5. 等待安装、注册和服务启动完成。
6. 返回控制台，确认节点在线并能浏览预期目录。

支持的发布矩阵为 Linux amd64/arm64、macOS amd64/arm64 和 Windows amd64。具体 Release 是否包含对应安装包，以该版本发布资产为准。

## 部署 Proxy

1. 打开代理主机管理页面，选择部署 Proxy。
2. 使用网络上能够访问 NAS、源端和目标仓库的 Linux 主机。
3. 运行向导生成的安装命令并等待注册完成。
4. 如需跨节点访问 Proxy 上的仓库，配置源端可达的 Repository Server 地址和允许的端口范围。
5. 添加 NAS 或本地磁盘时绑定该 Proxy，并执行存储验证。

Proxy 主机名或管理地址并不一定能被所有源端访问。跨网络环境应使用源端实际可达的地址，并配置路由、防火墙、安全组和 NAT。

## 验证节点

部署完成后至少检查：

- 节点状态为在线，版本与控制平面兼容。
- 平台、CPU、内存和磁盘清单已上报。
- Agent 可以浏览测试目录并验证路径。
- Proxy 可以挂载目标 NAS，或访问配置的本地磁盘目录。
- 服务重启后节点能够自动重新连接。

注册失败或节点离线时，进入[安装与节点排障](/zh/docs/troubleshooting/installation-nodes)。

