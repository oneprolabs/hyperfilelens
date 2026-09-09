---
title: Install HyperFileLens Community
description: Install and run HyperFileLens Community on your own Ubuntu host.
---

# Install HyperFileLens Community

HyperFileLens Community runs on an Ubuntu host that you manage. The online installer downloads and starts the latest published release.

## Before you install

| Item | Requirement |
| --- | --- |
| Operating system | Ubuntu 20.04, 22.04, or 24.04 on amd64 |
| CPU and memory | Minimum: 4 CPU cores and 8 GiB of memory; recommended: 8 cores and 16 GiB or more |
| Disk space | At least 20 GiB of free space on the disk that contains `/opt` |
| Container runtime | Docker Engine 24.0.0 or later and Docker Compose V2 2.20.0 or later, with the Docker daemon running |
| Required tools | `curl`, Python 3, and `sudo` access |
| Network access | Access to GitHub, the container registry, Docker CE package sources, and Ubuntu package repositories |
| Service ports | `11442–11445/TCP` available on the installation host |

::: info Docker environment

- **Install or reuse:** If Docker is not installed, the installer can install Docker CE and Compose V2. It reuses an existing installation when it meets the requirements above. If only Compose V2 is missing, the installer adds it only when the existing Docker packages can remain unchanged.
- **Supported runtime:** Docker CE is the only supported runtime. Ubuntu `docker.io`, Moby, Snap, and unrecognized runtimes must be addressed manually. The installer does not replace or repair an existing Docker installation.
- **Uninstall behavior:** Uninstalling HyperFileLens does not remove Docker, Compose, containerd, or a Docker CE package source configured by the installer.

:::

## Run the installer

On the prepared Ubuntu host, run:

```bash
curl -fsSL https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror global --yes
```

The installer displays the release version and download sources, then continues automatically. Wait for the installation to finish and the services to start.

## Check the installation

Run the following command to check the service status:

```bash
sudo /opt/hyperfilelens/install.sh status
```

Confirm that all core services are running or healthy. If installation fails, record the error shown in the terminal, verify the prerequisites above, and then run the installer again.

After a successful installation, the `Access` section lists these endpoints:

| Endpoint | Purpose |
| --- | --- |
| `HyperFileLens` | Product console for backup, restore, Insights, and organization administration |
| `Platform Ops` | Platform administration console for AI models, system configuration, and operations |

For first-time use, copy the `URL` listed under `HyperFileLens` and open it in your browser. Sign in with the `Email` and `Password` shown in the same section, then change the initial password immediately. Backup, restore, and Insights workflows run in the product console. Open `Platform Ops` only to configure AI models or perform platform administration.
