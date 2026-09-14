---
title: Configure AI models
description: Connect AI models and set the default Agent and multimodal models used by Insights.
---

# Configure AI models

A platform administrator configures the AI models used by Insights. The default Agent model handles conversation, reasoning, and answer generation. A default multimodal model is also required when analyzing images, scanned PDFs, or images embedded in documents.

| Model role | Requirement | Purpose |
| --- | --- | --- |
| Default Agent Model | Required | Conversation, reasoning, tool use, and answer generation |
| Default Multimodal Model | When needed | Images, scanned PDFs, and images embedded in documents |

The first text-only session requires only a default Agent model. If one model satisfies both the conversation and vision requirements, it can be assigned to both default roles; otherwise, configure separate models.

## Before you start

Prepare:

- a platform administrator account;
- the AI provider and model ID;
- a custom API base URL, if the provider requires one;
- a valid API key for the model service;
- network access from the HyperFileLens deployment to the model endpoint.

## Open AI Models

1. In the `Access` section of the installation output, find `Platform Ops` and open its `URL` in your browser.
2. Sign in with a platform administrator account.
3. Open **AI Engine → AI Models**.
4. Select **Add AI Model**.

## Add the default Agent model

1. Select the model provider.
2. Select a model or enter its model ID.
3. Enter the **API Key**. If required, enter the provider's custom **API Base URL**; otherwise, leave it blank to use the default endpoint.
4. Keep the model **Active**.
5. Select **Test Connection** and confirm that the connection succeeds.
6. Select **Add Model**.
7. In the model list, select the model, then choose **More Actions → Set as Default Agent Model**.

## Configure the default multimodal model

When you need to analyze images, scanned PDFs, or images embedded in documents:

1. Add and test a model with vision capability. You can reuse the default Agent model if it also supports vision.
2. Keep the model **Active**.
3. In the model list, select the model, then choose **More Actions → Set as Default Multimodal Model**.
4. Create a small test session with a document that contains an image or scanned page and confirm that its visual content is processed.

## Confirm readiness

Before continuing, confirm that:

- the default Agent model is **Active** and marked **Default Agent Model**;
- when visual content is required, the multimodal model is **Active** and marked **Default Multimodal Model**;
- connection tests succeed for both configured roles;
- **New Chat** opens without a missing-model warning.

API keys are sensitive. Enter them only in the product configuration and do not include them in screenshots, documentation, or public issues.
