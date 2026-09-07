---
title: Deployment guide
description: Deploy the HyperFileLens control plane, backup components, and Private Data Gateways for your environment.
---

# Deployment guide

<p class="hfl-doc-lead">Run the HyperFileLens Community control plane on an Ubuntu host in your environment. Deploy Agents, Proxies, and Private Data Gateways as needed to reach your backup sources, target storage, and snapshot data.</p>

## Deploy HyperFileLens Community

1. Review the [system requirements](/en/docs/deployment/requirements).
2. Plan the required [network connections and ports](/en/docs/deployment/network).
3. Follow [Install HyperFileLens Community](/en/docs/getting-started/install).
4. Complete the [post-installation checks](/en/docs/deployment/post-install).

## Deploy components

- [Deploy an Agent](/en/docs/deployment/agent) on a Windows, Linux, or macOS host to read local files and run backup and restore jobs.
- [Deploy a Proxy](/en/docs/deployment/proxy) on a network that can reach NAS or local storage and provide storage access for backup and restore jobs.
- [Deploy a Private Data Gateway](/en/docs/deployment/data-gateway) on a network that can reach a private backup repository when the Public Data Gateway cannot. The gateway prepares selected snapshot data for Insights.

## Operate HyperFileLens Community

- Use the installer to [upgrade HyperFileLens Community and recover from upgrade failures](/en/docs/deployment/lifecycle).
- Use [jobs, alerts, and audit logs](/en/docs/deployment/operations) to monitor day-to-day operation and investigate exceptions.
