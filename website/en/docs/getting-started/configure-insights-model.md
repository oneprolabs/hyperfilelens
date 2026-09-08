---
title: Configure an AI model for Insights
description: Connect an AI model and set the default Agent model required by Insights.
---

# Configure an AI model for Insights

Insights requires a default Agent model before it can create a session. A platform administrator configures this model in the Admin Console. If your deployment already has a ready default Agent model, confirm its status and continue to the next page.

## Before you start

Prepare:

- a platform administrator account;
- the AI provider and model ID;
- a custom API base URL, if the provider requires one;
- a valid API key for the model service;
- network access from the HyperFileLens deployment to the model endpoint.

## Open AI Models

1. In the `Access` section of the installation output, find `Platform Ops · 11444` and open its `URL` in your browser.
2. Sign in with a platform administrator account.
3. Open **AI Engine → AI Models**.
4. Select **Add AI Model**.

## Add the model

1. Select the model provider.
2. Select a model or enter its model ID.
3. Enter the **API Key**. If required, enter the provider's custom **API Base URL**; otherwise, leave it blank to use the default endpoint.
4. Keep the model **Active**.
5. Select **Test Connection** and confirm that the connection succeeds.
6. Select **Add Model**.
7. In the model list, select the model, then choose **AI Model Actions → Set as Default Agent**.

For the first text-based Insights session, only the default Agent model is required. Configure a default multimodal model later if you need to analyze images, scanned PDFs, or images embedded in documents.

## Confirm readiness

Before continuing, confirm that:

- the model is **Active**;
- it is marked **Default Agent**;
- the connection test succeeds;
- **New Chat** opens without a missing-model warning.

API keys are sensitive. Enter them only in the product configuration and do not include them in screenshots, documentation, or public issues.
