---
title: Deployment Guide
description: Deploy the HyperFileLens control plane, backup components, and Private Data Gateways for your environment.
---

# Deployment Guide

<p class="hfl-doc-lead">What you need to deploy depends on how you use HyperFileLens and how data is reached. The official SaaS does not require a self-hosted control plane. Community runs the control plane in your environment. Both options may use Agents, Proxies, and Private Data Gateways based on the location of backup sources, target storage, and snapshot data.</p>

## Choose what to deploy

<div class="hfl-deployment-grid">
  <section class="hfl-deployment-card">
    <small>Official hosting</small>
    <strong>Official SaaS</strong>
    <dl>
      <div><dt>Control plane</dt><dd>Hosted by OnePro Cloud; no deployment or maintenance is required.</dd></div>
      <div><dt>Agent</dt><dd>Install on each Windows, Linux, or macOS host whose files you want to protect.</dd></div>
      <div><dt>Proxy</dt><dd>Deploy on a network that can reach the NAS or local storage you want to use.</dd></div>
      <div><dt>Private Data Gateway</dt><dd>Use the Public Data Gateway by default. Deploy a private gateway when it cannot reach a repository on a private network.</dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>Self-hosted</small>
    <strong>Community</strong>
    <dl>
      <div><dt>Control plane</dt><dd>Install on an Ubuntu host in your environment and maintain it yourself.</dd></div>
      <div><dt>Agent</dt><dd>Install on each Windows, Linux, or macOS host whose files you want to protect.</dd></div>
      <div><dt>Proxy</dt><dd>Deploy on a network that can reach the NAS or local storage you want to use.</dd></div>
      <div><dt>Private Data Gateway</dt><dd>Community includes a Public Data Gateway. Add a private gateway only when the public gateway cannot reach a private repository.</dd></div>
    </dl>
  </section>
</div>

## Deploy Community

1. Review the [system requirements](/en/docs/deployment/requirements).
2. Plan the required [network connections and ports](/en/docs/deployment/network).
3. Follow [Install Community](/en/docs/getting-started/install).
4. Complete the [post-installation checks](/en/docs/deployment/post-install).

## Deploy components

- [Deploy an Agent](/en/docs/deployment/agent) on a Windows, Linux, or macOS host to read local files and run backup and restore jobs.
- [Deploy a Proxy](/en/docs/deployment/proxy) on a network that can reach NAS or local storage and provide storage access for backup and restore jobs.
- [Deploy a Private Data Gateway](/en/docs/deployment/data-gateway) on a network that can reach a private backup repository when the Public Data Gateway cannot. The gateway prepares selected snapshot data for Insights.

## Operate Community

- Use the installer to [upgrade and recover Community](/en/docs/deployment/lifecycle).
- Use [jobs, alerts, and audit logs](/en/docs/deployment/operations) to monitor day-to-day operation and investigate exceptions.
