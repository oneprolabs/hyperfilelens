---
title: 使用 Private Data Gateway
description: 选择并使用能够访问备份仓库的 Private Data Gateway。
---

# 使用 Private Data Gateway

默认使用平台提供的 Public Data Gateway。公共服务无法访问私有网络中的仓库，或者快照准备必须在组织自管网络中运行时，再部署 Private Data Gateway。

## 检查当前租户

打开 **Insights → Data Gateways**。该页面列出租户自己管理的 Private Data Gateway；为会话自动选择的 Public Data Gateway 不会作为租户自管节点出现在这里。

![租户 Data Gateways 空列表，个人账户已经模糊处理](/docs/insights/data-gateways-empty.png)

列表为空表示当前没有可供手动选择的 Private Data Gateway，不表示平台 Public Data Gateway 不可用。

## 部署私有网关

选择 **Add**。当前安装页要求 Ubuntu 20.04 LTS 或更高版本的 amd64 主机，至少具备 2 核 CPU、4 GB 内存和 50 GB 存储。该主机需要访问：

- HyperFileLens 控制平面的 HTTPS/WSS 地址；
- Insights 使用的对象存储，或连接 NAS/本地仓库的 Proxy；
- 已配置的 AI 模型 Endpoint；
- 所需的 DNS、时间和证书服务。

按照页面说明在目标主机运行安装命令。命令包含短期注册 Token 和组织信息，完整命令属于秘密，禁止粘贴到文档、Issue、聊天或日志中。

![Add Private Data Gateway 显示系统要求和安装阶段，注册命令已经完全不透明覆盖，个人账户已经模糊处理](/docs/insights/add-private-gateway.png)

详细部署步骤请查看 [Private Data Gateway 部署](/zh/docs/deployment/data-gateway)和[网络与端口](/zh/docs/deployment/network)。

## 使用前检查

- Gateway Agent 和 AI Engine 在线；
- 页面能够报告操作系统、CPU、内存、磁盘、容量、版本和注册时间；
- 网关能够读取将用于 Insights 的仓库；
- 工作区磁盘能够容纳选定数据范围；
- DNS、系统时间和 TLS 信任正确。

打开网关详情，确认 **AI Engine** 为 **AI Engine Online**。详情还会显示支持的任务、Engine 版本、最后心跳、注册时间和系统容量。

![Private Data Gateway 详情显示 AI Engine Online，主机、IP、MAC 地址、Source 和 Node 标识、Gateway 名称及 workspace 标识已经模糊处理](/docs/insights/private-gateway-detail.png)

## 在会话中选择

创建 AI Copilot 会话时选择 **Private Data Gateway**，再从可用列表中选择已经验证的网关。等待文件数量和大小计算完成，提交前核对快照时间、单文件测试范围、分析类型和 Gateway 类型。

![为单文件 Knowledge Q&A 会话选择 Private Data Gateway，账户、源端、主机和 Gateway 标识已经模糊处理，快照时间、文件数量和大小保持可见](/docs/insights/private-gateway-chat-ready.png)

等待会话变为 **Ready**，再提出答案已知的问题。本次正式验证通过 Private Data Gateway 恢复合成 CSV，正确返回了 3 台设备及其源行引用。

![Ready 状态的 Private Data Gateway 会话正确列出 3 台合成设备和源行，账户、源端、主机和 Gateway 标识已经模糊处理](/docs/insights/private-gateway-chat-answer.png)

Private Data Gateway 控制恢复和文档准备的位置，但不会让外部 AI 模型 Endpoint 自动变成私有服务。
