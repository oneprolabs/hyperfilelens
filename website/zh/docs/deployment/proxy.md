---
title: 部署 Proxy
description: 在共享存储所在网络中安装、注册和检查 HyperFileLens Proxy。
---

# 部署 Proxy

Proxy 部署在能够访问 NAS 或本地存储的 Linux 主机上，使 HyperFileLens 可以将这些存储用作备份源或目标存储。

## 部署前准备

- 准备一台符合[系统要求](/zh/docs/deployment/requirements)的 Ubuntu amd64 主机。
- 确认该主机能够连接 HyperFileLens 控制平面，并能访问计划使用的 NAS 或本地磁盘。
- 使用 NAS 时，确认 SMB 或 NFS 服务地址和端口可以从 Proxy 主机访问，具体请查看[网络与端口](/zh/docs/deployment/network)。

## 安装 Proxy

1. 打开<span class="hfl-path">数据保护 → 源端资源 → 代理主机</span>。
2. 选择<span class="hfl-ui">添加</span>，进入代理主机部署向导。
3. 生成安装命令，并按照向导提示在目标主机上使用管理员权限运行。
4. 等待安装完成，返回代理主机页面确认主机已经注册。

## 连接存储

添加 NAS 备份源、NAS 目标存储或 Proxy 本地磁盘时，选择已经部署的代理主机，并完成连接验证。

控制台默认使用 Proxy 自动上报的地址。仅当备份主机或 Private Data Gateway 无法通过该地址访问 Proxy 存储时，才需要编辑代理主机的<span class="hfl-ui">存储仓库服务器地址</span>，填写这些组件实际能够访问的地址，并开放相应端口。

## 验证部署

在控制台确认：

- 代理主机状态为在线。
- 绑定的 NAS 或本地磁盘连接验证通过。
- 可以从对应的备份源或目标存储页面访问预期目录。
- 主机或 Proxy 服务重启后能够自动重新连接。

注册失败、存储验证失败或 Proxy 离线时，进入[安装与节点](/zh/docs/troubleshooting/installation-nodes)。
