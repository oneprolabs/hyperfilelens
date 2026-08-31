---
title: Insights
description: Analyze selected backup snapshot data with HyperFileLens AI Copilot.
---

# Insights

HyperFileLens Insights works from protected backup snapshots. A Data Gateway prepares only the files or folders selected for a Chat, and AI Copilot answers questions about that fixed data scope. It does not read the live source directory.

## Usage flow

1. [Prepare a snapshot](/en/docs/insights/prepare) and confirm that the required files are available.
2. [Create an Insights session](/en/docs/insights/copilot), choosing a snapshot, data scope, analysis type, and Data Gateway.
3. Ask a question with an answer that can be checked against the selected files and citations.
4. Have a platform administrator [configure AI models](/en/docs/insights/models) when the required models are not ready.
5. [Use a Private Data Gateway](/en/docs/insights/data-gateway) only when the Public Data Gateway cannot reach the repository or processing must remain in a managed network.
6. Understand current [AI usage visibility](/en/docs/insights/usage) and manage the [session and data scope](/en/docs/insights/privacy).

![AI Copilot answer based on a synthetic CSV with account, host, and Gateway identifiers blurred while the cited result remains visible](/docs/getting-started/chat-answer.png)

## What Insights is suitable for

Good uses include finding facts in documents, summarizing policies, comparing versions, and analyzing a selected source-code tree. Start with a small, explicit scope and a question whose result can be independently checked.

AI output may be incomplete or incorrect. Verify citations, important numbers, dates, legal terms, security conclusions, and production commands against the original snapshot files before acting.
