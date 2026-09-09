---
title: Uninstall HyperFileLens Community
description: Remove the complete HyperFileLens Community deployment, or remove the runtime while retaining configuration and business data.
---

# Uninstall HyperFileLens Community

Use the installed management script to uninstall HyperFileLens. Do not delete containers, images, or the installation directory manually, because doing so can leave the deployment in an incomplete state.

## Before uninstalling

1. Wait for active backup, restore, and Insights jobs to finish.
2. Decide whether to retain the current configuration, business data, system backups, and logs.
3. Confirm that the Docker daemon is running.

## Choose an uninstall method

### Remove everything

```bash
sudo /opt/hyperfilelens/install.sh uninstall
```

This command starts immediately. It removes the HyperFileLens-managed runtime, configuration, business data, system backups, logs, and the `/opt/hyperfilelens` installation directory. This action cannot be undone, so move any data that you need to retain before running it.

### Retain persistent data

```bash
sudo /opt/hyperfilelens/install.sh uninstall --keep-data
```

This command removes the runtime and application files but retains configuration, business data, system backups, and logs. To restore the application later, run the Community online installation command again.

## What uninstall does not remove

- Docker CE, Docker Compose, and containerd installed on the host.
- Containers, images, and networks that are not managed by HyperFileLens.
- Agents and Proxies installed on other hosts, which have independent lifecycles.
