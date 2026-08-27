---
title: Deploy a Private Data Gateway
description: Deploy a Private Data Gateway for HyperFileLens Insights.
---

# Deploy a Private Data Gateway

A Data Gateway reads the snapshot files selected by a user and prepares them for Insights. It does not read live files directly from a protected host.

## Public and Private Data Gateways

- A **Public Data Gateway** is the default. It is platform-provided in the official SaaS and included with a Community installation.
- A **Private Data Gateway** runs on a network you manage. Use one when the Public Data Gateway cannot reach the backup repository or when data processing must remain in your network.

If the Public Data Gateway can reach the repository, you normally do not need to deploy a private gateway.

## Before deployment

- Prepare an Ubuntu 20.04, 22.04, or 24.04 amd64 host with at least 2 CPU cores, 4 GiB of memory, and 50 GiB of free space.
- Confirm that the host can reach both the HyperFileLens control plane and the required backup repositories. See [Network and Ports](/en/docs/deployment/network).
- If Docker is not installed, the installer adds the runtime included with the release. If Docker is already present, use Docker Engine 24.0.0 or later and Compose V2 2.20.0 or later.

## Deploy the gateway

1. Open <span class="hfl-path">Insights → Data Gateways</span>.
2. Select <span class="hfl-ui">Add Data Gateway</span> and choose a Private Data Gateway.
3. Review the requirements and generate the installation command.
4. Run the command with administrator privileges on the target host as instructed.
5. Wait for installation to finish, then return to Data Gateways and confirm that the gateway is registered.

## Verify the gateway

In the console, confirm that:

- The Private Data Gateway is online and its AI engine is ready.
- The gateway can reach the repositories that will be used by Insights.
- A test session can select the gateway and prepare data from the selected snapshot.

If installation fails, the gateway remains offline, or the AI engine is unhealthy, see [Installation and Nodes](/en/docs/troubleshooting/installation-nodes).
