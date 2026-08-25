---
title: 快速开始
description: 使用官方 SaaS 或安装 HyperFileLens Community，完成首次备份、恢复和智能洞察。
---

# 快速开始

<p class="hfl-doc-lead">选择官方 SaaS 或 Community，以 Windows 主机和对象存储为例，依次完成备份、恢复和智能洞察。</p>

<div class="hfl-doc-grid">
  <a class="hfl-doc-card" href="/zh/docs/getting-started/saas">
    <small>无需部署</small>
    <strong>使用官方 SaaS</strong>
    <span>注册或登录后直接进入控制台，适合快速体验完整业务流程。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/getting-started/install">
    <small>自托管</small>
    <strong>安装 Community</strong>
    <span>在自己的 Ubuntu 主机上安装当前公开版本，再进入控制台。</span>
  </a>
</div>

## 本次示例

准备一台 Windows 测试主机，并创建一个内容和大小都容易确认的目录，例如：

```text
C:\HFL-Quickstart\
├─ project-summary.txt
├─ device-inventory.csv
└─ incident-report.pdf
```

同时准备一个专用于本次测试的对象存储桶或对象前缀。不要使用包含其他业务数据的现有目录，也不要直接选择整个系统盘或大规模生产目录。

Windows 和对象存储只是本章采用的示例。其他源端与目标存储类型可在[备份与恢复](/zh/docs/backup-restore/)中查询。

## 完成首次使用

官方 SaaS 和 Community 只在进入产品前不同。登录控制台后，按照同一条流程操作：

1. [登录控制台](/zh/docs/getting-started/sign-in)。
2. [添加备份源](/zh/docs/getting-started/add-source)，将 Windows 主机接入控制台。
3. [配置备份源](/zh/docs/getting-started/configure-source)，选择需要保护的测试目录。
4. [添加目标存储](/zh/docs/getting-started/add-target)，在向导中创建并选择对象存储。
5. [创建并运行首次备份](/zh/docs/getting-started/first-backup)。
6. [检查任务与快照](/zh/docs/getting-started/verify-backup)，确认预期文件已经进入快照。
7. [恢复测试文件](/zh/docs/getting-started/first-restore)，验证备份数据确实可用。
8. [创建洞察会话](/zh/docs/getting-started/first-insight)，基于同一快照分析测试文件。

## 完成标准

同时满足下面的结果，才表示首次使用完成：

- Windows 备份源在线，并且能够浏览测试目录。
- 对象存储连接验证通过。
- 备份任务成功，并生成包含预期文件的可浏览快照。
- 测试文件已恢复到独立目录，文件内容与备份前一致。
- 已基于同一快照创建洞察会话，并获得可以回到原文件核对的回答。

完成后，可按需查阅[产品使用](/zh/docs/product/)、[部署运维](/zh/docs/deployment/)和[帮助中心](/zh/docs/help/)。
