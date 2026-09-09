---
title: Configure external access
description: Configure a reachable HyperFileLens URL when remote users, Agents, or Data Gateways connect through NAT, a public address, or a domain.
---

# Configure external access

Skip this page if users, Agents, and Data Gateways can all reach HyperFileLens through the address shown by the installer. Complete these steps before adding a remote backup source when connections must cross NAT or use a public IP address, domain, or reverse proxy.

## Decide whether configuration is required

| Scenario | Required |
| --- | --- |
| Users and all components share a private network and can use the installed address | No |
| A remote Agent or Data Gateway must connect through NAT | Yes |
| Users access the tenant console through a public IP address, domain, or reverse proxy | Yes |

The external access URL is the common control-plane entry point for remote components. New Agent and Data Gateway installation commands, enrollment callbacks, and related service connections use this address after it is configured.

## Prepare a reachable address

Complete the required network configuration outside HyperFileLens:

1. Forward the public IP address or domain to the tenant console service port. A reverse proxy can map standard HTTPS to the Community tenant console.
2. Configure the firewall to allow only the users, Agents, and Data Gateways that need access.
3. When using HTTPS, make sure the certificate matches the external domain and is trusted by remote hosts.
4. From an external network, verify that the address accepts HTTPS and WebSocket connections.

::: warning Configuration boundary

The HyperFileLens external access setting does not configure NAT, DNS, a reverse proxy, TLS certificates, or firewall rules. Make the address reachable before saving it. Keep the Admin Console and Insights service administration endpoints restricted to a management network; configuring tenant external access does not require exposing them to the public Internet.

:::

## Set the external access URL

1. In the `Access` section of the installation output, find `Platform Ops` and open its `URL` in your browser.
2. Sign in with a platform administrator account.
3. Open **Platform → External Access**.
4. Enter the address that remote users and components can reach in **External access URL**, for example `https://hfl.example.com`.
5. Enter only the origin containing the scheme, host, and optional port. Do not include a path, query, or fragment.
6. Save the configuration and confirm that **Effective URL** shows the expected address.

If the correct public address was supplied during installation, the page shows the installation configuration and an additional override is normally unnecessary.

## Verify external access

Before adding a backup source, confirm that:

- the tenant console opens and accepts a sign-in from the external network;
- newly generated Agent or Data Gateway installation commands contain the external access URL;
- a component installed on a remote host can enroll and remain online;
- backup, restore, and Insights pages still open in the tenant console.

If verification fails, check NAT forwarding, DNS, certificates, and firewall rules first. Then see [Network and ports](/docs/deployment/network) and [Installation and nodes](/docs/troubleshooting/installation-nodes).
