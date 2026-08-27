---
title: Deploy an Agent
description: Install, register, and verify a HyperFileLens Agent on a protected host.
---

# Deploy an Agent

Install an Agent on each Windows, Linux, or macOS host whose local files you want to protect. The Agent reads source files and runs backup and restore jobs.

## Before deployment

- Confirm that the host meets the [system requirements](/en/docs/deployment/requirements).
- Confirm that it can reach the HyperFileLens control plane and the target storage you plan to use. See [Network and Ports](/en/docs/deployment/network).
- Use an account with access to the files you want to protect. The installer determines how the Agent runs from the identity used to execute the command.

## Install the Agent

1. Open <span class="hfl-path">Protection → Source Resources</span>.
2. Select <span class="hfl-ui">Add Source Host</span> to open the deployment wizard.
3. Choose the operating system of the target host.
4. Generate the installation command and run it on the target host as the user whose files you want to protect. Use administrator privileges when the Agent must continuously protect host-wide data.
5. Wait for installation to finish, then return to Source Resources and confirm that the host is registered.

## Verify the Agent

In the console, confirm that:

- The source host is online.
- You can browse the required folders and select the intended backup paths.
- For a persistent installation, the Agent reconnects automatically after the host or service restarts.

If registration fails or the Agent remains offline, see [Installation and Nodes](/en/docs/troubleshooting/installation-nodes).
