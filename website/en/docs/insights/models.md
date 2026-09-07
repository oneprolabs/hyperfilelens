---
title: Configure AI models
description: Understand model readiness and the platform administrator's model configuration responsibilities.
---

# Configure AI models

Insights requires a default Agent model that is ready to use. A default multimodal model is also required when a session needs to process images, scanned PDFs, or images embedded in documents.

## Who configures models

AI models are managed by a platform administrator in the platform operations console, which is separate from the organization console. Contact the platform administrator if the **New Chat** page reports that a required model is unavailable.

## Administrator workflow

1. Open the platform AI model settings.
2. Add a model and choose the provider.
3. Enter the display name, provider model ID, API base URL, and credential.
4. Test connectivity and save the configuration.
5. Keep the model active and set an appropriate model as the default Agent model.
6. When visual understanding is required, set a compatible active model as the default multimodal model.
7. Create a small test session and verify both the answer and its citations.

API keys and provider credentials are secrets. Keep them out of screenshots, GitHub issues, terminal history, and documentation. If a screenshot is unavoidable, leave credential fields empty or cover each value completely with an opaque block; do not rely on blurring to hide a secret.

## User-visible readiness

Organization members can confirm that the models are ready from the product interface:

- **New Chat** opens without a missing-model warning;
- the intended analysis type is available;
- **Start Chat** becomes available after all required fields are selected;
- preparation reaches **Ready** and a test question returns an answer.

Before replacing or disabling a default model, configure its replacement and verify it with a test session. The provider's location, terms, and log-retention policy remain part of the organization's data-processing decision, even when a Private Data Gateway is used.
