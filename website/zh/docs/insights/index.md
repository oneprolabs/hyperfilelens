---
title: 智能洞察
description: 从 HyperFileLens 备份快照创建和使用 AI Copilot 会话。
---

# 智能洞察

智能洞察基于已经生成的备份快照工作。Data Gateway 只准备当前会话明确选择的文件或目录，AI Copilot 围绕这一固定范围回答问题，不直接读取源端的实时目录。

## 使用流程

1. [准备快照](/zh/docs/insights/prepare)，确认需要的文件确实存在。
2. [创建洞察会话](/zh/docs/insights/copilot)，选择快照、数据范围、分析类型和 Data Gateway。
3. 提出可以通过所选文件和引用核对的问题。
4. 所需模型未就绪时，由平台管理员[配置 AI 模型](/zh/docs/insights/models)。
5. 公共网关无法访问仓库或数据准备必须留在自管网络时，[使用 Private Data Gateway](/zh/docs/insights/data-gateway)。
6. 了解当前版本的 [AI 使用量可见范围](/zh/docs/insights/usage)，并管理[会话与数据范围](/zh/docs/insights/privacy)。

![AI Copilot 基于合成 CSV 返回答案，账户、主机和 Gateway 标识已经模糊处理，引用结果保持可见](/docs/getting-started/chat-answer.png)

适合的任务包括从文档中查找事实、总结制度、比较版本或分析选定的源码目录。应先选择边界清晰的小范围，并提出可以独立核对的问题。

AI 输出可能不完整或不准确。涉及重要数字、日期、合同条款、安全结论和生产命令时，必须通过引用和快照原文件复核后再采取行动。
