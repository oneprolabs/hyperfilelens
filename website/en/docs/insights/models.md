---
title: Configure AI Models
description: Understand model readiness and the platform administrator's model configuration responsibilities.
---

# Configure AI Models

Insights requires a ready default Agent model. A default multimodal model is also required when a Chat must understand images, scanned PDFs, or images embedded in documents.

## Who configures models

Model configuration is a platform-administration capability. Official SaaS tenant users do not manage provider credentials from the tenant **Insights** navigation. Contact the platform administrator when the New Chat page reports that a required model is unavailable.

Community or platform administrators use the platform operations console to manage AI models. The exact access URL depends on the deployment and is intentionally separate from the tenant console.

## Administrator workflow

1. Open the platform AI model settings.
2. Add a model and choose the provider.
3. Enter the display name, provider model ID, API base URL, and credential.
4. Test connectivity and save the configuration.
5. Keep the model active and set an appropriate model as the default Agent model.
6. When visual understanding is required, set a compatible active model as the default multimodal model.
7. Create a small test Chat and verify both the answer and citations.

API keys and provider credentials are secrets. Keep them out of screenshots, Issues, terminal history, and documentation. If a screenshot is unavoidable, leave credential fields empty or cover the complete value with fully opaque pixels; never blur a secret.

## User-visible readiness

Tenant users validate configuration through product behavior:

- **New Chat** opens without a missing-model warning;
- the intended analysis type is available;
- **Start Chat** becomes available after all required fields are selected;
- preparation reaches **Ready** and a test question returns an answer.

Before replacing or disabling a default model, configure its replacement and verify a test Chat. Provider location, terms, and log retention remain part of the organization's data-processing decision even when a Private Data Gateway is used.
