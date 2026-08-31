---
title: 网络与端口
description: 配置 HyperFileLens 控制平面、Agent、Proxy、Private Data Gateway 和存储之间的网络连接。
---

# 网络与端口

<p class="hfl-doc-lead">请按实际使用方式开放控制台、组件和存储之间的必要连接。官方 SaaS 使用公开 HTTPS 地址；社区版默认使用以下本机端口，也可以通过反向代理统一映射到标准 HTTPS 端口。</p>

## 社区版默认端口

<div class="hfl-deployment-grid">
  <section class="hfl-deployment-card">
    <small>11442/TCP</small>
    <strong>产品网站与文档</strong>
    <dl><div><dt>开放范围</dt><dd>按需向用户开放</dd></div></dl>
  </section>
  <section class="hfl-deployment-card">
    <small>11443/TCP</small>
    <strong>租户控制台与组件连接</strong>
    <dl><div><dt>开放范围</dt><dd>用户访问控制台所使用的网络，以及 Agent、Proxy 和 Private Data Gateway 的部署网络</dd></div></dl>
  </section>
  <section class="hfl-deployment-card">
    <small>11444/TCP</small>
    <strong>平台运维与系统管理</strong>
    <dl><div><dt>开放范围</dt><dd>仅限管理网络</dd></div></dl>
  </section>
  <section class="hfl-deployment-card">
    <small>11445/TCP</small>
    <strong>智能洞察服务管理</strong>
    <dl><div><dt>开放范围</dt><dd>仅限管理网络</dd></div></dl>
  </section>
</div>

安装社区版时，部署主机上的 `11442–11445/TCP` 必须未被其他程序占用。使用域名和反向代理后，浏览器及组件可以通过映射后的 `443/TCP` 访问，具体以实际配置的地址为准。社区版默认部署的公共 Data Gateway 与控制平面运行在同一主机，无需额外开放公网端口。

## 组件连接路径

<div class="hfl-deployment-grid">
  <section class="hfl-deployment-card">
    <small>控制连接</small>
    <strong>控制平面</strong>
    <dl>
      <div><dt>浏览器</dt><dd>访问官方 SaaS <code>443/TCP</code>，或社区版 <code>11443/TCP</code> 及其映射端口</dd></div>
      <div><dt>Agent、Proxy、Private Data Gateway</dt><dd>通过官方 SaaS 的 <code>443/TCP</code> 或社区版的 <code>11443/TCP</code> 建立 HTTPS/WSS 连接，用于注册、状态上报和任务控制</dd></div>
      <div><dt>社区版部署主机</dt><dd>通过 <code>443/TCP</code> 访问 Gitee、镜像仓库和 Ubuntu 软件源，完成在线安装与升级</dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>数据连接</small>
    <strong>备份与恢复</strong>
    <dl>
      <div><dt>对象存储</dt><dd>控制平面和执行备份或恢复任务的 Agent、Proxy 连接对象存储 Endpoint；端口以 Endpoint 配置为准，HTTPS 通常使用 <code>443/TCP</code></dd></div>
      <div><dt>NAS</dt><dd>Proxy 连接 NAS 所需端口：SMB 使用 <code>445/TCP</code>，NFS 使用 <code>2049/TCP</code></dd></div>
      <div><dt>跨主机访问 Proxy 存储</dt><dd>备份主机或 Private Data Gateway 访问 Proxy 连接的 NAS 或本地存储时，允许访问 Proxy 的 <code>51515–52014/TCP</code></dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>数据分析</small>
    <strong>智能洞察</strong>
    <dl>
      <div><dt>备份仓库</dt><dd>Private Data Gateway 读取快照时，连接所选对象存储，或通过 Proxy 访问其连接的 NAS 或本地存储</dd></div>
      <div><dt>AI 模型服务</dt><dd>智能洞察服务连接已配置的模型 Endpoint；端口以模型服务地址为准，HTTPS 通常使用 <code>443/TCP</code></dd></div>
    </dl>
  </section>
</div>

Agent、Proxy 和 Private Data Gateway 均由所在网络主动连接控制平面，通常无需为这些组件开放来自控制平面的入站端口。仅当备份主机或 Private Data Gateway 需要跨主机访问 Proxy 连接的 NAS 或本地存储时，才需允许其访问 Proxy 的 `51515–52014/TCP`。

## 配置原则

- 按实际连接路径开放必要端口，并限制允许访问的来源网络。
- `11444/TCP` 和 `11445/TCP` 仅向管理网络开放；Proxy 的 `51515–52014/TCP` 仅向需要跨节点访问仓库的 Agent 或 Private Data Gateway 开放。
- 对象存储 Endpoint 必须可达，但存储桶无需公开；使用专用访问凭据并授予最小必要权限。
- 建议保持 TLS 校验开启，避免通过长期关闭校验或放开全部防火墙规则解决连接问题。
- 网络策略变更后，依次确认组件在线、目标存储可访问，并验证备份、恢复和智能洞察功能。
