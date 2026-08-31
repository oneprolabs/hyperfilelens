---
title: Use a Private Data Gateway
description: Deploy and select a tenant-managed Data Gateway that can reach a private backup repository.
---

# Use a Private Data Gateway

Use the platform-provided Public Data Gateway by default. Deploy a Private Data Gateway when the public service cannot reach a repository in a private network, or when snapshot preparation must run in a network managed by the organization.

## Check the current tenant

Open **Insights → Data Gateways**. This tenant page lists tenant-managed Private Data Gateways; the Public Data Gateway selected automatically for a Chat does not appear as a tenant-managed row.

![Empty tenant Data Gateways list with the personal account blurred](/docs/insights/data-gateways-empty.png)

An empty list means no Private Data Gateway is available for manual selection. It does not mean that the platform Public Data Gateway is unavailable.

## Deploy a private Gateway

Select **Add**. The current deployment page requires an Ubuntu 20.04 LTS or newer amd64 host with at least 2 CPU cores, 4 GB memory, and 50 GB storage. Place the host where it can reach:

- the HyperFileLens control plane over HTTPS/WSS;
- each repository used by Insights, directly or through its Proxy;
- the configured AI model endpoint;
- required DNS, time, and certificate services.

Run the generated command as instructed on the intended host. The enrollment command contains a short-lived registration Token and organization information. Treat the complete command as a secret: do not paste it into documentation, Issues, chat, or logs.

![Add Private Data Gateway showing system requirements and installation stages with the enrollment command fully covered and the personal account blurred](/docs/insights/add-private-gateway.png)

See [Deploy a Private Data Gateway](/en/docs/deployment/data-gateway) and [Network and Ports](/en/docs/deployment/network) for deployment details.

## Validate before use

Return to **Insights → Data Gateways** and confirm:

- the Gateway Agent and AI engine are online;
- the OS, CPU, memory, disks, capacity, version, and registration time are reported;
- the host can read the intended repository;
- workspace storage is sufficient for the selected scope.

Open the Gateway details and confirm that **AI Engine** is **AI Engine Online**. The detail view also reports supported tasks, engine version, last heartbeat, registration time, and system capacity.

![Private Data Gateway details showing AI Engine Online with the host, IP, MAC address, Source and Node identifiers, Gateway name, and workspace identifier blurred](/docs/insights/private-gateway-detail.png)

## Verify with a Chat

Create a Chat, select **Private Data Gateway**, choose the validated Gateway, and wait for the selected file count and size to finish calculating. Confirm the snapshot time, one-file test scope, analysis type, and Gateway type before selecting **Start Chat**.

![Private Data Gateway selected for a one-file Knowledge Q&A Chat with account, source, host, and Gateway identifiers blurred while snapshot time, file count, and size remain visible](/docs/insights/private-gateway-chat-ready.png)

Wait for the Chat to become **Ready**, then ask a question with a known answer. The verified test restored a synthetic CSV through the Private Data Gateway and correctly returned all three device rows and their source-line references.

![Ready Private Data Gateway Chat correctly listing three synthetic devices and source rows with account, source, host, and Gateway identifiers blurred](/docs/insights/private-gateway-chat-answer.png)

A private Gateway controls where restore and document preparation run; it does not make an external model endpoint private.
