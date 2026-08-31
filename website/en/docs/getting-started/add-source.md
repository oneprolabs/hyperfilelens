---
title: Add a backup source
description: Register a Windows host as a HyperFileLens backup source.
---

# Add a backup source

This step installs the Agent on the Windows host that contains `C:\HFL-Quickstart`, registers the host with HyperFileLens, and confirms that it is online. You will select the directory to protect in the next step.

This guide uses `C:\HFL-Quickstart` and its sample files to demonstrate the complete workflow. In actual use, replace them with the directories and files you want to protect, then adapt the restore verification and Insights questions to the selected content. You do not need to recreate the exact sample dataset used in this guide.

## Before you start

- You are signed in to HyperFileLens with the interface language set to **English**.
- Your account can add backup sources.
- The Windows host is online and can run PowerShell.
- The directory you want to protect is ready; this guide uses `C:\HFL-Quickstart` as its example.

## Open the Windows installation page

1. Open **Protection → Backup Wizard**.
2. Make sure **Backup Sources** is the current step, then select **Add Source**.
3. Select **Source Host**.
4. Under **Select Target Operating System**, select **Windows**.

The Windows installation command appears under **Run the Install Command**.

![Add a Windows backup source with the registration command redacted](/docs/getting-started/add-windows-source.png)

## Install the Windows Agent

1. Select **Click to copy** next to the installation command.
2. On the Windows host, press **Win + R**.
3. Enter `powershell`, then press **Enter**.
4. Paste the copied command into PowerShell and press **Enter**.
5. Wait for the installer to report `Installation completed successfully` and `Node is online in HyperFileLens`.

The command may contain short-lived registration information. Do not reuse a command from an old screenshot or publish the complete command in documentation, chat, or a public issue.

![Windows Agent installation completed with user paths and installation details redacted](/docs/getting-started/windows-agent-installed.png)

## Confirm that the host is online

Return to **Protection → Backup Wizard** and select the refresh button above the source table. Confirm that the new source shows:

- **Host · Windows** as its type;
- **Registered** under **Lifecycle Status**; and
- **Online** under **Connectivity**.

![Registered and online Windows backup source with the hostname, IP address, account, and registration time redacted](/docs/getting-started/windows-source-online.png)

## Completion criteria

- PowerShell reports that installation succeeded and the node is online.
- The Windows host appears in the **Backup Sources** table.
- **Lifecycle Status** is **Registered**.
- **Connectivity** is **Online**.

Next: [Configure the backup source](/en/docs/getting-started/configure-source) and select `C:\HFL-Quickstart`.
