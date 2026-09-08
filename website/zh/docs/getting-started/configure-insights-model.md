---
title: 配置洞察模型
description: 连接 AI 模型，并设置智能洞察所需的默认 Agent 模型。
---

# 配置洞察模型

创建洞察会话前，需要由平台管理员在管理控制台配置默认 Agent 模型。如果当前环境已经存在可用的默认 Agent 模型，确认其状态后即可继续下一步。

## 开始前准备

准备以下信息：

- 平台管理员账户；
- AI 模型供应商和模型 ID；
- 模型供应商要求使用的自定义 API 地址（如有）；
- 模型服务的有效 API Key；
- HyperFileLens 部署环境能够连接模型服务。

## 打开 AI Models

1. 在安装结果的 `Access` 区域找到 `Platform Ops · 11444`，并在浏览器中打开其 `URL`。
2. 使用平台管理员账户登录。
3. 打开 **AI Engine → AI Models**。
4. 选择 **Add AI Model**。

## 添加模型

1. 选择模型供应商。
2. 选择模型或填写模型 ID。
3. 填写 **API Key**。模型供应商要求使用自定义地址时填写 **API Base URL**；否则保持为空，使用供应商默认地址。
4. 保持模型为 **Active**。
5. 选择 **Test Connection**，确认连接成功。
6. 选择 **Add Model**。
7. 在模型列表中选中该模型，然后选择 **AI Model Actions → Set as Default Agent**。

首次使用文本文件创建洞察会话时，只需要默认 Agent 模型。后续需要分析图片、扫描 PDF 或文档内图片时，再配置默认多模态模型。

## 确认模型可用

继续前确认：

- 模型状态为 **Active**；
- 模型标记为 **Default Agent**；
- 连接测试成功；
- 打开 **New Chat** 时不再提示缺少模型。

API Key 属于敏感信息，只能填写在产品配置中，不要出现在截图、文档或公开 Issue 中。
