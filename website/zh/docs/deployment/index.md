---
title: 部署指南
description: 根据使用方式部署 HyperFileLens 控制平面、备份组件和 Private Data Gateway。
---

# 部署指南

<p class="hfl-doc-lead">部署范围取决于使用方式和数据访问路径。官方 SaaS 无需部署控制平面；Community 需要在自有环境安装控制平面。两种方式均可按备份源、目标存储和智能洞察的网络要求部署 Agent、Proxy 或 Private Data Gateway。</p>

## 确定部署范围

<div class="hfl-deployment-grid">
  <section class="hfl-deployment-card">
    <small>官方托管</small>
    <strong>官方 SaaS</strong>
    <dl>
      <div><dt>控制平面</dt><dd>由 OnePro Cloud 托管，无需自行部署。</dd></div>
      <div><dt>Agent</dt><dd>保护主机文件时，安装在需要保护的 Windows、Linux 或 macOS 主机上。</dd></div>
      <div><dt>Proxy</dt><dd>接入 NAS 或本地存储时，部署在能够访问相应存储的网络中。</dd></div>
      <div><dt>Private Data Gateway</dt><dd>默认使用公共 Data Gateway；备份仓库位于私有网络且公共网关无法访问时部署。</dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>自托管</small>
    <strong>Community</strong>
    <dl>
      <div><dt>控制平面</dt><dd>部署在自有 Ubuntu 主机，并自行维护运行环境。</dd></div>
      <div><dt>Agent</dt><dd>保护主机文件时，安装在需要保护的 Windows、Linux 或 macOS 主机上。</dd></div>
      <div><dt>Proxy</dt><dd>接入 NAS 或本地存储时，部署在能够访问相应存储的网络中。</dd></div>
      <div><dt>Private Data Gateway</dt><dd>安装时默认部署公共 Data Gateway；备份仓库位于私有网络且公共网关无法访问时，再部署私有网关。</dd></div>
    </dl>
  </section>
</div>

## 部署 Community

1. 查看[系统要求](/zh/docs/deployment/requirements)。
2. 根据实际环境规划[网络与端口](/zh/docs/deployment/network)。
3. 按照[安装 Community](/zh/docs/getting-started/install)完成安装。
4. 完成[安装后检查](/zh/docs/deployment/post-install)。

## 组件部署

- [部署 Agent](/zh/docs/deployment/agent)：安装在需要保护的 Windows、Linux 或 macOS 主机上，用于访问源端文件并执行备份与恢复任务。
- [部署 Proxy](/zh/docs/deployment/proxy)：部署在能够访问 NAS 或本地存储的网络中，为备份与恢复提供存储连接和数据访问能力。
- [部署 Private Data Gateway](/zh/docs/deployment/data-gateway)：当公共 Data Gateway 无法访问私有备份仓库时，将其部署在仓库可达的网络中，让智能洞察能够访问所选快照并准备分析数据。

## 运行维护

- Community 管理员使用安装程序完成[升级与恢复](/zh/docs/deployment/lifecycle)。
- 日常运行中通过[任务、告警与审计](/zh/docs/deployment/operations)检查运行状态和异常信息。
