---
title: 创建洞察会话
description: 基于首次备份快照创建洞察会话并使用 Public Data Gateway。
---

# 创建洞察会话

智能洞察使用已经生成的备份快照，不直接读取 Windows 主机上的生产目录。本次继续使用首次备份生成的快照和 `device-inventory.csv`。

## 开始前检查

- 首次备份快照状态为 **可用**，并包含 `insights\device-inventory.csv`。
- 已配置并启用默认 Agent 模型。
- 当前环境存在可用的 **公共数据网关**。
- 当前账户拥有创建 AI Copilot 会话的权限。

## 打开 New Chat

1. 打开 **智能洞察 → AI Copilot**。
2. 在会话列表上方选择 **新建会话**。首次使用且没有历史会话时，主区域可能显示 **开始新会话**。

## 选择备份数据

1. 在 **数据源** 中选择刚才的备份源。
2. 选择同一个已验证的快照，或保留 **最新可用快照** 并确认它对应本次快照。
3. 在 **文件和文件夹** 中添加 `C:\HFL-Quickstart\insights\device-inventory.csv`。
4. 确认右侧摘要显示来源为 **受保护快照**，并且数据范围为 1 个文件、132 B。

![新建会话页面显示数据源、快照、文件和文件夹以及分析类型选项，账户标识已经模糊处理](/docs/zh/getting-started/insights-select-data.png)

## 选择分析类型和 Data Gateway

1. 在 **分析类型** 中选择 **知识问答（推荐）**。
2. 在 **数据隐私** 中选择 **公共数据网关**。
3. 确认页面显示所选快照、文件范围和 Public Gateway。
4. 本次不选择 **私有数据网关**；当前页面显示没有在线的私有数据网关。

![新建对话已选择知识问答和公共数据网关，账户、主机和 Gateway 标识已模糊处理](/docs/zh/getting-started/insights-gateway-ready.png)

## 创建会话

1. 选择 **开始会话**。
2. 等待数据准备完成，然后再发送问题。

![Insights 会话已创建并显示 Ready，账户、主机和 Gateway 标识已经模糊处理](/docs/zh/getting-started/chat.png)

首个问题应当能够直接从 CSV 文件核对，例如：

```text
这个文件中列出了多少台设备？
请列出每台设备的名称及其状态。
```

预期答案为 3 台设备：Atlas（Active）、Beacon（Active）和 Cedar（Inactive）。回答中的数字、名称和状态应回到原始 CSV 文件人工核对。

![AI Copilot 根据 CSV 返回 3 台设备及其状态，账户、主机和 Gateway 标识已经模糊处理](/docs/zh/getting-started/chat-answer.png)

如果按钮不可用或数据准备失败，先确认快照、文件范围、Public Data Gateway 和默认 AI 模型是否就绪，不要连续重复提交相同请求。

至此，首次智能洞察验证完成：会话状态为 **就绪**，回答列出了 3 台设备及其状态，并与 `device-inventory.csv` 的第 2–4 行一致。
