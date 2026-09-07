---
title: Insights
description: Analyze selected backup snapshot data with HyperFileLens AI Copilot.
---

# Insights

HyperFileLens Insights analyzes protected backup snapshots. A Data Gateway prepares only the files or folders selected for a session, and AI Copilot answers questions about that fixed data scope. It does not read live files from the source host.

## Workflow

1. [Prepare a snapshot](/docs/insights/prepare) and confirm that the required files are available.
2. [Create an Insights session](/docs/insights/copilot), choosing a snapshot, data scope, analysis type, and Data Gateway.
3. Ask a question whose answer can be checked against the selected files and citations.
4. Have a platform administrator [configure AI models](/docs/insights/models) when the required models are not ready.
5. [Use a Private Data Gateway](/docs/insights/data-gateway) only when the Public Data Gateway cannot reach the repository or processing must remain in a managed network.
6. Understand and manage the [session and data scope](/docs/insights/privacy).

![AI Copilot answer based on a synthetic CSV with account, host, and Gateway identifiers blurred while the cited result remains visible](/docs/getting-started/chat-answer.png)

## When to use Insights

Insights can help you find facts in documents, summarize policies, compare versions, and analyze a selected source-code tree. Start with a small, clearly defined scope and a question whose answer can be independently checked.

AI output may be incomplete or incorrect. Verify citations, important numbers, dates, legal terms, security conclusions, and production commands against the original snapshot files before acting.
