---
title: 创建洞察会话
description: 选择备份源、快照、数据范围和网关，创建 HyperFileLens 洞察会话。
---

# 创建洞察会话

打开 **Insights → AI Copilot** 并选择 **New Chat**。每个会话都绑定一个受保护快照和明确的文件或目录范围。

## 创建会话

1. 在 **Data Source** 中选择已有备份配置。
2. 选择 **Latest available snapshot** 或指定快照时间点。
3. 在 **Files and Folders** 中浏览快照并添加至少一个文件或目录。
4. 选择分析类型：
   - **Knowledge Q&A**：搜索、总结并回答选定文档中的问题；
   - **Code Analysis**：分析源码结构、依赖关系和实现逻辑。
5. 在 **Data Privacy** 中保留 **Public Data Gateway** 让平台自动选择，或者手动选择在线 **Private Data Gateway**。
6. 核对右侧摘要并选择 **Start Chat**。

![New Chat 空白表单显示受保护快照、Knowledge Q&A、Code Analysis 和 Public Data Gateway 区域，账户和 Gateway 标识已经模糊处理](/docs/insights/new-chat.png)

![为 Insights 会话选择合成快照文件，账户、主机和仓库标识已经模糊处理](/docs/getting-started/insights-select-data.png)

在备份源、快照、数据范围、分析类型和网关全部有效前，创建按钮会保持禁用。按页面提示解决条件，不要连续重复提交相同请求。

## 等待数据准备

Data Gateway 会把所选快照范围恢复到隔离工作区并为会话准备数据。会话可能显示排队、准备中、Ready 或失败。等待状态变为 **Ready** 后再提问。

![Knowledge Q&A 已配置 Public Data Gateway，并显示 Private Gateway 可用情况，账户、主机和 Gateway 标识已经模糊处理](/docs/getting-started/insights-gateway-ready.png)

## 提问方式

先从范围明确、可以核对的问题开始，并说明输出格式、时间范围或比较标准：

- “列出此目录中的主要文档及其主题。”
- “这份协议的有效期和终止条件是什么？请给出依据。”
- “比较两个版本目录中的关键变更。”
- “定位实现某项功能的主要文件，并说明调用关系。”

范围过大的问题应拆成多轮，并要求给出引用。

## 使用回答

- 打开引用或相关文件，确认回答与原文一致。
- 对重要数字、日期、责任边界和操作命令进行人工复核。
- 回答缺少依据时，缩小问题范围或明确要求引用。
- 回答似乎来自旧内容时，核对会话绑定的快照时间。即使源端文件后来发生变化，会话仍然绑定创建时选择的快照。

AI Copilot 适合辅助理解和定位，不应成为未经复核的生产变更、法律或财务决策依据。
