---
title: Session and Data Scope
description: Understand how a Chat binds a snapshot, selected data, Data Gateway, and AI model.
---

# Session and Data Scope

Each Chat binds the backup source, one snapshot point, selected files or folders, analysis type, and Data Gateway chosen at creation time. Renaming a Chat or continuing its message history does not change that underlying data boundary.

## Data flow

1. A backup task writes protected source data to the repository.
2. The user chooses a specific snapshot and file or folder scope.
3. The selected Data Gateway restores and prepares that scope in an isolated workspace.
4. AI Copilot and the configured model process the content needed for the Chat.
5. The answer can include citations pointing back to the selected snapshot data.

Whether content leaves a private network depends on the Gateway location, model endpoint, provider, and deployment configuration together. A Private Data Gateway keeps restore and preparation on that Gateway; it does not imply that an external model API is also private.

## Verify answers and citations

Return to the snapshot file whenever:

- an answer contains an exact number, date, contract term, or security conclusion;
- selected files disagree;
- a citation does not support the conclusion;
- the snapshot is older than the live production data;
- the answer could trigger a production, legal, or financial action.

![Ready AI Copilot Chat bound to a protected snapshot with account, host, and Gateway identifiers blurred while the snapshot time and selected data remain visible](/docs/getting-started/chat.png)

## Manage history safely

The session list contains historical questions and answers. Before sharing a screenshot, inspect the Chat name, question, answer, citations, paths, file names, account, host, repository, and Gateway identifiers.

Deleting or renaming a Chat is not the same as deleting a source snapshot, and deleting a snapshot should not be assumed to remove logs retained by an external model provider. Follow organizational retention and access-revocation procedures across the account, snapshot, Chat, Gateway workspace, and provider.
