---
title: 部署 Private Data Gateway
description: 部署用于 HyperFileLens 智能洞察的 Data Gateway。
---

# 部署 Private Data Gateway

Data Gateway 从备份仓库读取用户选择的快照文件，并为智能洞察准备数据。它不会直接读取备份主机上的实时文件。

## 公共与私有 Data Gateway

- **公共 Data Gateway**是默认使用方式。官方 SaaS 由平台提供；社区版在安装时默认部署。
- **私有 Data Gateway**部署在用户管理的网络中，适合备份仓库无法由公共网关访问，或数据处理需要保留在自有网络中的场景。

如果公共 Data Gateway 能够访问备份仓库，通常无需额外部署私有网关。

## 部署前准备

- 准备一台 Ubuntu 20.04、22.04 或 24.04 amd64 主机，至少配备 2 核 CPU、4 GiB 内存和 50 GiB 可用空间。
- 确认主机能够连接 HyperFileLens 控制平面和需要访问的备份仓库，具体请查看[网络与端口](/zh/docs/deployment/network)。
- 主机未安装 Docker 时，安装程序会安装随版本提供的运行环境；已经安装 Docker 时，需要 Docker Engine 24.0.0 及以上版本和 Compose V2 2.20.0 及以上版本。

## 部署步骤

1. 打开<span class="hfl-path">洞察 → 数据网关</span>。
2. 选择<span class="hfl-ui">添加</span>，进入私有数据网关部署向导。
3. 核对系统要求，生成安装命令。
4. 按照向导提示，在目标主机上使用管理员权限运行安装命令。
5. 等待安装完成，返回数据网关页面确认网关已经注册。

## 验证部署

在控制台确认：

- 私有数据网关状态为在线，AI 引擎状态正常。
- 网关可以访问计划用于智能洞察的备份仓库。
- 创建测试洞察会话时，可以选择该网关并完成所选快照的数据准备。

安装失败、网关离线或 AI 引擎状态异常时，进入[安装与节点](/zh/docs/troubleshooting/installation-nodes)。
