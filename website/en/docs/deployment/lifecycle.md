---
title: Upgrade and recovery
description: Check, back up, and upgrade HyperFileLens Community, and respond safely to upgrade failures.
---

# Upgrade and recovery

Use the installer to check the status of HyperFileLens Community, create system backups, and perform upgrades. Do not manually replace runtime files or container images in the installation directory.

## Before upgrading

1. Read the target release notes and confirm that your current version meets its upgrade requirements.
2. Choose a maintenance window and wait for running backup, restore, and maintenance jobs to finish.
3. Confirm that the control-plane services are healthy.

Check the current version and service status:

```bash
sudo /opt/hyperfilelens/install.sh status
```

The upgrade process automatically creates and validates a system backup before making changes. To create a backup before other important maintenance, run:

```bash
sudo /opt/hyperfilelens/install.sh backup
```

The installer retains the three most recent valid system backups. These backups protect the Community control plane's configuration and operational data. They do not include data from protected sources and do not replace backup and restore testing in the product.

## Run the upgrade

### Online upgrade

Run the Community online installation command again to upgrade from the global download source:

```bash
curl -fsSL https://raw.githubusercontent.com/oneprolabs/hyperfilelens/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror global --yes
```

The installer detects the existing Community deployment, retrieves the latest release, and starts the managed upgrade workflow.

### Upgrade from a release package

If you already have the release package for the target version, provide its full path:

```bash
sudo /opt/hyperfilelens/install.sh upgrade \
  --from /path/to/hyperfilelens-vX.Y.Z.tar.gz
```

The installer validates the package and system backup before upgrading and checking the services. Do not close the installation terminal, stop services manually, or change the installation directory during the upgrade.

## Verify the upgrade

1. Run `status` again and confirm the version and service health.
2. Sign in and open the main product areas.
3. Confirm that Agents, Proxies, and Data Gateways are healthy.
4. Run a representative backup or restore job and confirm that it completes.

## Respond to an upgrade failure

If an upgrade fails, the installer attempts to restore the pre-upgrade services but does not automatically restore the database backup. The current release does not support downgrading by installing an older package or simply switching to older images.

After a failure:

1. Do not repeat the upgrade, delete system backups, or replace runtime files manually.
2. Run `status` and record the current version and unhealthy services.
3. Keep the installer log and system backups.
4. Follow the recovery instructions in the target release notes. If none are provided, see [Installation and nodes](/docs/troubleshooting/installation-nodes).
