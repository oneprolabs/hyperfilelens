---
title: Supported Configurations
description: Review platforms, backup sources, target storage, and Insights capabilities supported by HyperFileLens Community.
---

# Supported Configurations

This page summarizes the primary platforms and capabilities supported by HyperFileLens Community. Support can vary by release, so also review the release notes for the version you plan to deploy.

## Runtime platforms

| Component | Operating system | Architecture |
| --- | --- | --- |
| Community control plane | Ubuntu 20.04, 22.04, or 24.04 | amd64 |
| Agent | Linux | amd64, arm64 |
| Agent | Windows | amd64 |
| Agent | macOS | amd64, arm64 |
| Proxy | Ubuntu 20.04, 22.04, or 24.04 | amd64 |
| Private Data Gateway | Ubuntu 20.04, 22.04, or 24.04 | amd64 |

See [System Requirements](/en/docs/deployment/requirements) for CPU, memory, and disk requirements.

## Backup sources

| Type | Supported configuration |
| --- | --- |
| Host files | Linux, Windows, and macOS hosts running an Agent |
| NAS | SMB or NFS shares connected through a Proxy; a supported Linux backup host may connect directly only when the product explicitly offers that option |

HyperFileLens currently provides file-level backup. For a consistent copy of a database, virtual machine, or application, first use the relevant system or application tooling to create data suitable for file-level backup.

## Target storage

| Type | Supported configuration |
| --- | --- |
| Object storage | AWS S3, Alibaba Cloud OSS, Huawei Cloud OBS, and supported S3-compatible services |
| NAS | SMB or NFS shares connected through a Proxy; a supported Linux backup host may connect directly only when the product explicitly offers that option |
| Local storage | A dedicated directory on a Proxy host |

An S3-compatible service must support the S3 APIs, TLS behavior, addressing format, and permissions used by HyperFileLens. Implementations differ between providers, so validate connection, backup, and restore before production use.

## Insights

Insights uses available snapshots from existing backup configurations. Creating a session requires an available default AI model and an online Data Gateway. Image and scanned-document processing also requires a supported multimodal model.

File understanding depends on the current AI engine, configured model, and file condition. Encrypted, corrupted, oversized, or policy-excluded files may not be processed.
