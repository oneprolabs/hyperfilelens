---
title: Install Community
description: Install and run HyperFileLens Community on your own Ubuntu host.
---

# Install Community

HyperFileLens Community runs on an Ubuntu host that you manage. The online installer downloads and starts the latest published Community release.

## Before you install

- Ubuntu 20.04, 22.04, or 24.04 on amd64.
- At least 2 CPU cores and 4 GiB of memory. For regular use, 4 cores and 8 GiB or more are recommended.
- At least 20 GiB of free space on the disk that contains `/opt`.
- Docker Engine and Docker Compose V2 installed and running.
- `curl` and `sudo` access.
- Network access to GitHub, the container registry, and the Ubuntu package repositories.
- Ports `11442–11445` available on the installation host.

## Run the installer

On the prepared Ubuntu host, run:

```bash
curl -fsSL https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror global
```

The installer shows the release version and download sources before it starts. Review the information, then wait for the installation and services to finish starting.

### Install a specific release (optional)

To install a published version explicitly:

```bash
curl -fsSL https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror global --tag vX.Y.Z
```

Replace `vX.Y.Z` with the version you want to install.

## Check the installation

Run the following command to check the service status:

```bash
sudo /opt/hyperfilelens/install.sh status
```

The core services should report a running or healthy state. If installation fails, keep the error shown in the terminal, verify the prerequisites above, and then run the installer again.

When installation finishes, the terminal prints several access addresses. Copy the complete address marked `Tenant` and open it in your browser to enter the HyperFileLens console. The other addresses are for the website or system administration and are not needed for first-time use.

Sign in with the initial account shown by the installer, then change the initial password immediately.
