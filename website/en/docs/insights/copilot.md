---
title: Create an Insights Session
description: Select snapshot data and a Data Gateway, then create and use an AI Copilot Chat.
---

# Create an Insights Session

Open **Insights → AI Copilot** and select **New Chat**. Each Chat binds to one protected snapshot and an explicit file or folder scope.

## Create a Chat

1. Under **Data Source**, select a configured backup source.
2. Select **Latest available snapshot** or a specific snapshot point.
3. Under **Files and Folders**, browse the snapshot and add at least one file or directory.
4. Choose an analysis type:
   - **Knowledge Q&A** searches, summarizes, and answers questions about documents;
   - **Code Analysis** examines source structure, dependencies, and implementation logic.
5. Under **Data Privacy**, keep **Public Data Gateway** for automatic platform selection, or select an online **Private Data Gateway**.
6. Check the summary and select **Start Chat**.

![Empty New Chat form showing the protected-snapshot source, Knowledge Q&A, Code Analysis, and Public Data Gateway sections with account and Gateway identifiers blurred](/docs/insights/new-chat.png)

![Synthetic snapshot file selected for an Insights Chat with account, host, and repository identifiers blurred](/docs/getting-started/insights-select-data.png)

The creation button remains disabled until the required source, snapshot, data scope, analysis type, and Gateway are valid. Follow the page message instead of repeatedly submitting the same request.

## Wait for preparation

The selected Data Gateway restores the chosen snapshot scope into an isolated workspace and prepares it for the Chat. The session can show queued, preparing, ready, or failed states. Wait for **Ready** before asking questions.

![Knowledge Q&A configured with a Public Data Gateway and private Gateway availability shown, with account, host, and Gateway identifiers blurred](/docs/getting-started/insights-gateway-ready.png)

## Ask and verify

Begin with a narrow question that has a checkable answer. State the expected format, time range, or comparison criteria. For example:

- “List each device and its status. Cite the source rows.”
- “Summarize the termination conditions and cite the relevant section.”
- “Compare these two version folders and list the significant changes.”
- “Identify the main implementation files and explain the call path.”

Open citations and compare the answer with the snapshot files. If the evidence is weak, narrow the question or explicitly request citations. A Chat remains tied to its original snapshot even when live source files later change.
