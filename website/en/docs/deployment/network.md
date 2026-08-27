---
title: Network and Ports
description: Plan connectivity between the control plane, Agents, Proxies, Private Data Gateways, and storage.
---

# Network and Ports

<p class="hfl-doc-lead">Allow only the connections required by your deployment and data path. The official SaaS uses public HTTPS endpoints. Community uses the local ports below by default and can map them to standard HTTPS through a reverse proxy.</p>

## Default Community ports

<div class="hfl-deployment-grid">
  <section class="hfl-deployment-card">
    <small>11442/TCP</small>
    <strong>Product website and documentation</strong>
    <dl><div><dt>Access</dt><dd>Make available to users when needed</dd></div></dl>
  </section>
  <section class="hfl-deployment-card">
    <small>11443/TCP</small>
    <strong>Tenant console and component connections</strong>
    <dl><div><dt>Access</dt><dd>User networks and the networks where Agents, Proxies, and Private Data Gateways run</dd></div></dl>
  </section>
  <section class="hfl-deployment-card">
    <small>11444/TCP</small>
    <strong>Platform operations and system administration</strong>
    <dl><div><dt>Access</dt><dd>Management networks only</dd></div></dl>
  </section>
  <section class="hfl-deployment-card">
    <small>11445/TCP</small>
    <strong>Insights service administration</strong>
    <dl><div><dt>Access</dt><dd>Management networks only</dd></div></dl>
  </section>
</div>

Ports `11442–11445/TCP` must be free on the Community host during installation. With a domain and reverse proxy, browsers and components can use the mapped `443/TCP` endpoint instead. Use the address configured for your environment. The Public Data Gateway included with Community runs on the control-plane host and does not require another public port.

## Connection paths

<div class="hfl-deployment-grid">
  <section class="hfl-deployment-card">
    <small>Control traffic</small>
    <strong>Control plane</strong>
    <dl>
      <div><dt>Browsers</dt><dd>Connect to the official SaaS on <code>443/TCP</code>, or to Community on <code>11443/TCP</code> or its mapped port</dd></div>
      <div><dt>Agent, Proxy, and Private Data Gateway</dt><dd>Connect over HTTPS/WSS to SaaS <code>443/TCP</code>, or Community <code>11443/TCP</code>, for registration, status reporting, and job control</dd></div>
      <div><dt>Community host</dt><dd>Uses <code>443/TCP</code> to reach GitHub, container registries, and Ubuntu package repositories during online installation and upgrades</dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>Data traffic</small>
    <strong>Backup and restore</strong>
    <dl>
      <div><dt>Object storage</dt><dd>The control plane and the Agent or Proxy running a job connect to the object-storage endpoint. Use the configured endpoint port; HTTPS normally uses <code>443/TCP</code></dd></div>
      <div><dt>NAS</dt><dd>The Proxy connects to SMB on <code>445/TCP</code> or NFS on <code>2049/TCP</code></dd></div>
      <div><dt>Proxy storage from another host</dt><dd>Allow backup hosts or Private Data Gateways to reach <code>51515–52014/TCP</code> on the Proxy when they need its attached NAS or local storage</dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>Analysis traffic</small>
    <strong>Insights</strong>
    <dl>
      <div><dt>Backup repository</dt><dd>A Private Data Gateway reads the selected snapshot from object storage or through the Proxy attached to NAS or local storage</dd></div>
      <div><dt>AI model service</dt><dd>Insights connects to the configured model endpoint. Use the endpoint's configured port; HTTPS normally uses <code>443/TCP</code></dd></div>
    </dl>
  </section>
</div>

Agents, Proxies, and Private Data Gateways initiate their own connections to the control plane, so they normally do not need inbound access from it. Open `51515–52014/TCP` on a Proxy only when another backup host or Private Data Gateway must reach storage attached to that Proxy.

## Configuration guidelines

- Open only the ports used by the actual connection path and restrict their source networks.
- Limit `11444/TCP` and `11445/TCP` to management networks. Allow Proxy ports `51515–52014/TCP` only from Agents or Private Data Gateways that require cross-host repository access.
- Object-storage endpoints must be reachable, but buckets do not need to be public. Use dedicated credentials with the minimum required permissions.
- Keep TLS verification enabled where possible. Do not solve connectivity problems by permanently disabling verification or opening unrestricted firewall access.
- After changing network policy, confirm that components are online and target storage is reachable, then test backup, restore, and Insights.
