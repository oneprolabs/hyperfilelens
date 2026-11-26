---
title: Deployment guide
description: Deploy the HyperFileLens control plane, backup components, and Private Data Gateways for your environment.
---

# Deployment guide

<p class="hfl-doc-lead">Run the HyperFileLens Community control plane on an Ubuntu host in your environment. Deploy Agents, Proxies, and Private Data Gateways as needed to reach your backup sources, target storage, and snapshot data.</p>

## Deploy HyperFileLens Community

1. Review the [system requirements](/docs/deployment/requirements).
2. Plan the required [network connections and ports](/docs/deployment/network).
3. Follow [Install HyperFileLens Community](/docs/getting-started/install).
4. Complete the [post-installation checks](/docs/deployment/post-install).

## Deploy components

- [Deploy an Agent](/docs/deployment/agent) on a Windows, Linux, or macOS host to read local files and run backup and restore jobs.
- [Deploy a Proxy](/docs/deployment/proxy) on a network that can reach NAS or local storage and provide storage access for backup and restore jobs.
- [Deploy a Private Data Gateway](/docs/deployment/data-gateway) on a network that can reach a private backup repository when the Public Data Gateway cannot. The gateway prepares selected snapshot data for Insights.

## Operate HyperFileLens Community

- [Repository cache settings](/docs/deployment/repository-cache): adjust per-repository cache budgets for Agents and Proxies, including allocation, application timing, and disk-space planning.
- [Jobs, alerts, and audit logs](/docs/deployment/operations): review task status, alerts, and audit records; new organizations receive default alert rules. For members, roles, and quotas, see [Organization Management](/docs/governance/).
- [Upgrade and recovery](/docs/deployment/lifecycle): upgrade HyperFileLens Community, verify the result, and respond safely to upgrade failures.
- [Uninstall HyperFileLens Community](/docs/deployment/uninstall): remove the complete deployment, or remove the runtime while retaining configuration and business data.

## Personal Edition Operations

Authorized Personal Edition operators prepare managed object storage and temporary-credential issuance. This requires Enterprise and is not a setup task for ordinary personal users.

Start with [Managed Storage Overview](/docs/deployment/personal-edition/managed-storage/), then follow the least-privilege procedure for [Huawei Cloud OBS](/docs/deployment/personal-edition/managed-storage/huawei), [Alibaba Cloud OSS](/docs/deployment/personal-edition/managed-storage/aliyun), [AWS S3](/docs/deployment/personal-edition/managed-storage/aws), [RustFS](/docs/deployment/personal-edition/managed-storage/rustfs), or [MinIO](/docs/deployment/personal-edition/managed-storage/minio).
