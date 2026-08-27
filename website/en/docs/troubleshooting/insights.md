---
title: Insights and Data Gateway
description: Troubleshoot insight sessions, data preparation, AI models, and Data Gateways.
---

# Insights and Data Gateway

If a session cannot be created, start with the page message and check the snapshot, data scope, AI model, and Data Gateway.

## A session cannot be created

Confirm that:

- The selected backup configuration has an available snapshot.
- The selected snapshot succeeded or partially succeeded and contains backed-up files.
- At least one file or folder is selected from the snapshot.
- The platform has an available default AI model.
- An available Public Data Gateway is selected automatically, or an online Private Data Gateway is selected manually.

If <span class="hfl-ui">Automatic</span> reports that no Public Data Gateway is available, choose a Private Data Gateway or contact the administrator. Refreshing the page will not create a missing public gateway.

## A Private Data Gateway is unavailable

- Open <span class="hfl-path">Insights → Data Gateways</span> and confirm that the gateway and its AI engine are healthy.
- Confirm that the gateway can reach the control plane and the repository containing the selected snapshot.
- Confirm that its version is compatible with the control plane.
- Check free disk space on the gateway host.

If installation did not finish, see [Installation and Nodes](/en/docs/troubleshooting/installation-nodes).

## Data preparation does not finish

- Retry with a small set of files or a smaller folder.
- Check whether the selected files are oversized, corrupted, encrypted, or unsupported by the current parser.
- Check repository performance and Data Gateway health.
- Do not create several sessions for the same scope, which only adds concurrent load.

## An answer is incomplete or lacks evidence

- Confirm that the session is bound to the intended snapshot time.
- Confirm that the files are present in both the snapshot and the session's data scope.
- Narrow the question and explicitly request citations.
- Images and scanned PDFs require an available multimodal model.
- Verify important conclusions against the cited source material.

## Questions about data location

Confirm whether the session uses a Public or Private Data Gateway and who provides the AI model. Data-processing location depends on the gateway location, model endpoint, and organization network configuration together. A Private Data Gateway does not mean that an external model service also runs in the private network.
