---
title: System Requirements
description: Review the system requirements for Community, Agent, Proxy, and Private Data Gateway hosts.
---

# System Requirements

<p class="hfl-doc-lead">Before installing the Community control plane or another HyperFileLens component, verify that the target host meets the platform, resource, and runtime requirements below. The official SaaS does not require a control-plane host; check only the Agents, Proxies, or Private Data Gateways you deploy.</p>

## Community control plane

| Item | Minimum | Recommended |
| --- | --- | --- |
| Operating system | Ubuntu 20.04, 22.04, or 24.04 | Ubuntu 22.04 or 24.04 |
| Architecture | amd64 | amd64 |
| CPU | 2 cores | 4 or more cores |
| Memory | 4 GiB | 8 GiB or more |
| Free space on `/opt` | 20 GiB | 40 GiB |
| Docker Engine | 24.0.0 | 24.0.0 or later |
| Docker Compose | 2.20.0, Compose V2 | 2.20.0 or later |

The control plane is installed in `/opt/hyperfilelens`. The installer checks free space and reports insufficient CPU, memory, or swap. Use the recommended configuration for regular operation.

## Component platforms

<div class="hfl-deployment-grid">
  <section class="hfl-deployment-card">
    <small>Protected host</small>
    <strong>Agent</strong>
    <dl>
      <div><dt>Platforms</dt><dd>Linux amd64/arm64, macOS amd64/arm64, and Windows amd64</dd></div>
      <div><dt>Resources</dt><dd>2 or more CPU cores, 2 GiB or more memory, and 10 GiB or more free space</dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>Storage access</small>
    <strong>Proxy</strong>
    <dl>
      <div><dt>Platform</dt><dd>Ubuntu 20.04, 22.04, or 24.04 on amd64</dd></div>
      <div><dt>Resources</dt><dd>2 or more CPU cores, 4 GiB or more memory, and 50 GiB or more free space</dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>Insights</small>
    <strong>Private Data Gateway</strong>
    <dl>
      <div><dt>Platform</dt><dd>Ubuntu 20.04, 22.04, or 24.04 on amd64</dd></div>
      <div><dt>Resources</dt><dd>2 or more CPU cores, 4 GiB or more memory, and 50 GiB or more free space</dd></div>
    </dl>
  </section>
</div>

## Before installation

- The Community host has `sudo` access and `curl` installed.
- Docker Engine is running and can start containers.
- The host can reach GitHub, the container registry, and Ubuntu package repositories.
- Hosts used for online installation or component registration can resolve the required domains, establish HTTPS connections, and keep accurate system time.
- The required paths between the control plane, components, and storage are allowed. See [Network and Ports](/en/docs/deployment/network).
