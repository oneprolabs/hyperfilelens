---
title: HyperFileLens 用户文档
description: 从部署到首次备份、恢复和智能洞察，完成 HyperFileLens 的主要使用流程。
---

# HyperFileLens 用户文档

<p class="hfl-doc-lead">保护生产数据，并基于隔离的备份副本完成恢复和智能洞察。本手册以实际任务为主线，不要求你先了解全部产品概念。</p>

<div class="hfl-doc-grid">
  <a class="hfl-doc-card" href="/zh/docs/getting-started/">
    <small>快速开始</small>
    <strong>从安装到首次备份</strong>
    <span>准备控制平面，注册备份源，选择目标存储并生成第一个可用快照。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/backup-restore/">
    <small>数据保护</small>
    <strong>备份、快照与恢复</strong>
    <span>建立持续保护任务，验证快照，并把选定文件或目录恢复到可用节点。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/insights/">
    <small>智能洞察</small>
    <strong>从备份数据创建 Copilot 会话</strong>
    <span>选择快照和数据范围，通过 Data Gateway 准备数据并进行有依据的问答。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/deployment/">
    <small>部署与运维</small>
    <strong>管理 Agent、Proxy 与 Data Gateway</strong>
    <span>了解节点职责、支持平台、安装入口，以及版本升级和运行状态检查。</span>
  </a>
</div>

## 建议阅读顺序

第一次使用时，按下面的顺序完成一条最短闭环：

1. 查看[部署要求](/zh/docs/getting-started/requirements)，准备控制平面主机和备份存储。
2. [安装 HyperFileLens](/zh/docs/getting-started/install)，使用安装程序输出的地址登录。
3. [完成首次备份](/zh/docs/getting-started/first-backup)，确认至少产生一个成功或部分成功的快照。
4. [恢复文件和目录](/zh/docs/backup-restore/restore)，验证备份数据可以实际使用。
5. 如需对备份内容提问，继续完成[智能洞察](/zh/docs/insights/)流程。

## 文档边界

本手册面向 HyperFileLens Community 当前公开版本，介绍用户可见的产品行为和受支持流程。开发环境搭建、源码构建和贡献规范仍以仓库根目录的 README 为准；企业版授权、平台级治理和 SaaS 运营不属于本手册范围。

当界面、版本发布说明与本文档存在差异时，应先确认运行版本，并以该版本的发布说明和实际产品行为为准。

