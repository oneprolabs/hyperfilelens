---
title: Troubleshooting Guide
description: Locate HyperFileLens installation, node, backup, restore, and Insights problems by the stage that failed.
---

# Troubleshooting Guide

Use the message on the page and the job details to identify where the failure occurred before changing configuration. Changing several conditions at once makes the cause harder to isolate.

## Recommended sequence

1. Record the product version, time, page, and complete error message.
2. Open the related job or resource and identify the failed step and affected scope.
3. Confirm that the relevant Agent, Proxy, or Data Gateway is online.
4. Validate the backup source, target storage, or model-service connection separately.
5. Change one condition at a time, then repeat the original action.

## Choose a scenario

<div class="hfl-doc-grid">
  <a class="hfl-doc-card" href="/en/docs/troubleshooting/account-sign-in">
    <small>Account access</small>
    <strong>Console or sign-in unavailable</strong>
    <span>Check the console address, sign-in method, account state, and browser access.</span>
  </a>
  <a class="hfl-doc-card" href="/en/docs/troubleshooting/installation-nodes">
    <small>Installation and connectivity</small>
    <strong>Installation failed or a node is offline</strong>
    <span>Check system prerequisites, the installation command, component state, and control-plane connectivity.</span>
  </a>
  <a class="hfl-doc-card" href="/en/docs/troubleshooting/protection">
    <small>Data protection</small>
    <strong>Backup, repository, or restore failed</strong>
    <span>Check folder permissions, object storage, NAS, Proxy storage access, snapshots, and the restore destination.</span>
  </a>
  <a class="hfl-doc-card" href="/en/docs/troubleshooting/insights">
    <small>Insights</small>
    <strong>Data Gateway or AI Copilot unavailable</strong>
    <span>Check models, snapshot scope, the Public or Private Data Gateway, and data-preparation status.</span>
  </a>
</div>

## Before opening an issue

After confirming that the problem is reproducible, search [GitHub Issues](https://github.com/oneprolabs/hyperfilelens/issues) for an existing report. For a new issue, include the version, operating system, reproduction steps, and complete error message. Attach only the relevant, sanitized logs needed for investigation.
