---
title: Create an Insights session
description: Create an AI Copilot session from the first backup snapshot using a Public Data Gateway.
---

# Create an Insights session

Insights uses a protected backup snapshot, not the live Windows source. This step continues with the first snapshot and `device-inventory.csv`.

## Before you start

- The first snapshot is **Available** and contains `insights\device-inventory.csv`.
- A **Public Data Gateway** is available in the current environment.
- Your account can create an AI Copilot session.

## Open New Chat

1. Open **Insights → AI Copilot**.
2. Select **New Chat** above the Chat list. A first-time account may instead show **Start New Chat** in the main panel.

![Current AI Copilot page with AI Copilot under Applications, Data Gateways under AI Engine, and real account, host, and Gateway identifiers blurred](/docs/getting-started/insights-empty.png)

## Select the backup data

1. Under **Data Source**, select the backup source used for the first backup.
2. Select the same verified snapshot, or keep **Latest available snapshot** and confirm that it is the snapshot from this run.
3. Under **Files and Folders**, add `C:\HFL-Quickstart\insights\device-inventory.csv`.
4. Confirm that the summary shows **Protected snapshot** and a scope of one file and 132 B.

![New Chat with the backup source, snapshot, and device-inventory.csv selected; account, host, and Gateway details blurred](/docs/getting-started/insights-select-data.png)

## Choose the analysis type and Data Gateway

1. Under **Analysis Type**, select **Knowledge Q&A (Recommended)**.
2. Under **Data Privacy**, select **Public Data Gateway**.
3. Confirm the snapshot, file scope, and Public Gateway in the summary.
4. Do not select **Private Data Gateway** for this run; the page reports that no online Private Data Gateway is available.

![Knowledge Q&A and Public Data Gateway selected with account, host, and Gateway name blurred](/docs/getting-started/insights-gateway-ready.png)

## Start the session

1. Select **Start Chat**.
2. Wait for data preparation to finish before sending a question.

![Insights session created with Ready status; account, host, and Gateway identifiers blurred](/docs/getting-started/chat.png)

Start with a question that can be checked directly against the CSV file:

```text
How many devices are listed in this file?
Please list each device name and its status.
```

The expected answer is three devices: Atlas (Active), Beacon (Active), and Cedar (Inactive). Manually check the numbers, names, and statuses against the source CSV.

![AI Copilot answer listing three devices and their statuses; account, host, and Gateway identifiers blurred](/docs/getting-started/chat-answer.png)

If the button is unavailable or preparation fails, check the snapshot, file scope, Public Data Gateway, and default AI model before retrying. Do not repeatedly submit the same request.

The First Use Insights check is complete: the session is **Ready**, the answer lists three devices and their statuses, and the result matches rows 2–4 of `device-inventory.csv`.
