---
title: Deploy a Proxy
description: Install, register, and verify a HyperFileLens Proxy on a network with storage access.
---

# Deploy a Proxy

Deploy a Proxy on a Linux host that can reach the required NAS or local storage. HyperFileLens can then use that storage as a backup source or target.

## Before deployment

- Prepare an Ubuntu amd64 host that meets the [system requirements](/en/docs/deployment/requirements).
- Confirm that the host can reach the HyperFileLens control plane and the NAS or local disk you plan to use.
- For NAS, confirm that the SMB or NFS service and port are reachable from the Proxy host. See [Network and Ports](/en/docs/deployment/network).

## Install the Proxy

1. Open <span class="hfl-path">Protection → Source Resources → Proxy Hosts</span>.
2. Select <span class="hfl-ui">Add</span> to open the Proxy deployment wizard.
3. Generate the installation command and run it with administrator privileges on the target host as instructed.
4. Wait for installation to finish, then return to Proxy Hosts and confirm that the host is registered.

## Connect storage

When adding a NAS source, NAS target, or Proxy local-disk target, select the deployed Proxy and complete connection validation.

The console uses the address reported by the Proxy by default. Change <span class="hfl-ui">Repository Server Address</span> only when an Agent or Private Data Gateway cannot reach Proxy storage through that address. Enter an address those components can actually reach and allow the required ports.

## Verify the Proxy

In the console, confirm that:

- The Proxy Host is online.
- Connection validation succeeds for attached NAS or local storage.
- The expected folders are available from the relevant source or target page.
- The Proxy reconnects automatically after its host or service restarts.

If registration or storage validation fails, or the Proxy remains offline, see [Installation and Nodes](/en/docs/troubleshooting/installation-nodes).
