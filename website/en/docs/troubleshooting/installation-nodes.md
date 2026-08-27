---
title: Installation and Nodes
description: Troubleshoot control-plane installation and Agent, Proxy, or Data Gateway registration and connectivity.
---

# Installation and Nodes

Start with the error shown by the installer or console, then check the matching system and network conditions.

## Community installation fails

Check each of the following:

- The host runs Ubuntu 20.04, 22.04, or 24.04 on amd64.
- The installer was run through `sudo`.
- Docker Engine and Compose V2 meet the [system requirements](/en/docs/deployment/requirements) and are running.
- CPU, memory, and free space on `/opt` meet the requirements.
- No other process is using `11442–11445/TCP`.
- The host can reach GitHub, the container registry, and Ubuntu package repositories.

If the installer reports an incomplete earlier installation, do not overwrite the installation directory. Keep the complete message and determine whether the existing environment must be recovered before running the installer again.

## Component registration fails

- Generate a new installation command from the current console and run it before it expires.
- Confirm that the selected operating system matches the target. Proxy and Private Data Gateway require a supported Ubuntu amd64 host.
- Run an Agent as an identity that can read the required files. Run Proxy and Private Data Gateway installers with administrator privileges as instructed by the wizard.
- Confirm that the target host can resolve and reach the current control-plane address and trusts its TLS certificate.
- Check egress firewalls, security groups, and network proxies for the required connections.

## A component goes offline after installation

1. Refresh the component in the console and check its last-seen time and version.
2. Use the maintenance command provided in the component details to check its service and restart it when needed.
3. Check the component's HTTPS/WSS path to the control plane.
4. Confirm that the component version is compatible with the control plane.
5. If it remains offline, inspect its logs for network, certificate, or registration errors.

Do not delete the node data directory simply to register it again. If its local identity no longer matches the console, use the product's removal, repair, or redeployment workflow.

## Private Data Gateway installation fails

- Confirm that the host runs Ubuntu 20.04, 22.04, or 24.04 on amd64 and meets the CPU, memory, and disk requirements.
- If Docker is installed, confirm that Docker Engine and Compose V2 meet the required versions and are running.
- If Docker is not installed, confirm that the installer can use the runtime included with the current release.
- Confirm that the host can reach the control plane and the backup repositories it needs.
- Use the failed installer stage to check download access, free space, or AI engine installation.

See [Deploy a Private Data Gateway](/en/docs/deployment/data-gateway) for deployment requirements and steps.
