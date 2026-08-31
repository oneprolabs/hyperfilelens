---
title: 创建洞察会话
description: 基于首次备份快照创建洞察会话并使用 Public Data Gateway。
---

# 创建洞察会话

智能洞察使用已经生成的备份快照，不直接读取 Windows 主机上的生产目录。本次继续使用首次备份生成的快照和 `device-inventory.csv`。

## 开始前检查

- 首次备份快照状态为 **Available**，并包含 `insights\device-inventory.csv`。
- 当前环境存在可用的 **Public Data Gateway**。
- 当前账户拥有创建 AI Copilot 会话的权限。

## 打开 New Chat

1. 打开 **Insights → AI Copilot**。
2. 在空白页面选择 **Start New Chat**，或在左侧选择 **New Chat**。

![AI Copilot 空白页面，账户信息已经模糊处理](/docs/getting-started/insights-empty.png)

## 选择备份数据

1. 在 **Data Source** 中选择刚才的备份源。
2. 选择同一个已验证的快照，或保留 **Latest available snapshot** 并确认它对应本次快照。
3. 在 **Files and Folders** 中添加 `C:\HFL-Quickstart\insights\device-inventory.csv`。
4. 确认右侧摘要显示来源为 **Protected snapshot**，并且数据范围为 1 个文件、132 B。

![New Chat 中选择备份源、快照和 device-inventory.csv，账户、主机和 Gateway 信息已经模糊处理](/docs/getting-started/insights-select-data.png)

## 选择分析类型和 Data Gateway

1. 在 **Analysis Type** 中选择 **Knowledge Q&A (Recommended)**。
2. 在 **Data Privacy** 中选择 **Public Data Gateway**。
3. 确认页面显示所选快照、文件范围和 Public Gateway。
4. 本次不选择 **Private Data Gateway**；当前页面显示没有在线的 Private Data Gateway。

![选择 Knowledge Q&A 和 Public Data Gateway，账户、主机和 Gateway 名称已经模糊处理](/docs/getting-started/insights-gateway-ready.png)

## 创建会话

1. 选择 **Start Chat**。
2. 等待数据准备完成，然后再发送问题。

![Insights 会话已创建并显示 Ready，账户、主机和 Gateway 标识已经模糊处理](/docs/getting-started/chat.png)

首个问题应当能够直接从 CSV 文件核对，例如：

```text
How many devices are listed in this file?
Please list each device name and its status.
```

预期答案为 3 台设备：Atlas（Active）、Beacon（Active）和 Cedar（Inactive）。回答中的数字、名称和状态应回到原始 CSV 文件人工核对。

![AI Copilot 根据 CSV 返回 3 台设备及其状态，账户、主机和 Gateway 标识已经模糊处理](/docs/getting-started/chat-answer.png)

如果按钮不可用或数据准备失败，先确认快照、文件范围、Public Data Gateway 和默认 AI 模型是否就绪，不要连续重复提交相同请求。

至此，首次 Insights 验证完成：会话状态为 **Ready**，回答列出了 3 台设备及其状态，并与 `device-inventory.csv` 的第 2–4 行一致。
