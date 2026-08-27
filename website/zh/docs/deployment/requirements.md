---
title: 系统要求
description: 安装 HyperFileLens Community、Agent、Proxy 和 Private Data Gateway 前需要满足的系统条件。
---

# 系统要求

<p class="hfl-doc-lead">安装 Community 控制平面或相关组件前，请确认目标主机满足以下平台、资源和运行条件。官方 SaaS 无需部署控制平面，只需检查实际部署的 Agent、Proxy 或 Private Data Gateway。</p>

## Community 控制平面

| 项目 | 最低配置 | 推荐配置 |
| --- | --- | --- |
| 操作系统 | Ubuntu 20.04、22.04 或 24.04 | Ubuntu 22.04 或 24.04 |
| 架构 | amd64 | amd64 |
| CPU | 2 核 | 4 核以上 |
| 内存 | 4 GiB | 8 GiB 以上 |
| `/opt` 可用空间 | 20 GiB | 40 GiB |
| Docker Engine | 24.0.0 | 24.0.0 及以上 |
| Docker Compose | 2.20.0（Compose V2） | 2.20.0 及以上 |

控制平面安装在 `/opt/hyperfilelens`。安装程序会检查可用空间，并在 CPU、内存或交换空间（Swap）不足时给出提示。正式使用建议采用表中的推荐配置。

## 组件平台

<div class="hfl-deployment-grid">
  <section class="hfl-deployment-card">
    <small>备份主机</small>
    <strong>Agent</strong>
    <dl>
      <div><dt>支持平台</dt><dd>Linux amd64/arm64、macOS amd64/arm64、Windows amd64</dd></div>
      <div><dt>基础资源</dt><dd>2 核以上、2 GiB 以上内存、10 GiB 以上可用空间</dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>存储连接</small>
    <strong>Proxy</strong>
    <dl>
      <div><dt>支持平台</dt><dd>Ubuntu 20.04、22.04 或 24.04，amd64</dd></div>
      <div><dt>基础资源</dt><dd>2 核以上、4 GiB 以上、50 GiB 以上可用空间</dd></div>
    </dl>
  </section>
  <section class="hfl-deployment-card">
    <small>智能洞察</small>
    <strong>Private Data Gateway</strong>
    <dl>
      <div><dt>支持平台</dt><dd>Ubuntu 20.04、22.04 或 24.04，amd64</dd></div>
      <div><dt>基础资源</dt><dd>2 核以上、4 GiB 以上、50 GiB 以上可用空间</dd></div>
    </dl>
  </section>
</div>

## 安装前条件

- Community 安装主机具备 `sudo` 权限，并已安装 `curl`。
- Docker Engine 已启动并能够正常运行容器。
- 主机能够访问安装源、镜像仓库和 Ubuntu 软件源。
- 在线安装或连接控制平面时，主机能够解析相关域名并建立正常 HTTPS 连接，系统时间保持准确。
- 控制平面、各组件和存储之间的必要连接已放通，具体路径与端口请查看[网络与端口](/zh/docs/deployment/network)。
