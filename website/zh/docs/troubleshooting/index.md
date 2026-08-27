---
title: 排查方法
description: 按失败阶段定位 HyperFileLens 安装、节点、备份、恢复和智能洞察问题。
---

# 排查方法

先根据页面提示和任务详情确定错误发生的位置，再修改配置。一次修改多个条件，会增加定位问题的难度。

## 排障顺序

1. 记录产品版本、发生时间、页面位置和错误信息。
2. 打开相关任务或资源详情，确认失败步骤和影响范围。
3. 检查相关 Agent、Proxy 或 Data Gateway 是否在线。
4. 分别验证备份源、目标存储或模型服务的连接。
5. 每次只修改一个条件，然后重复原操作验证结果。

## 按场景进入

<div class="hfl-doc-grid">
  <a class="hfl-doc-card" href="/zh/docs/troubleshooting/account-sign-in">
    <small>账户访问</small>
    <strong>无法打开或登录控制台</strong>
    <span>确认控制台地址、登录方式、账户状态和浏览器访问。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/troubleshooting/installation-nodes">
    <small>安装与连接</small>
    <strong>安装失败或节点离线</strong>
    <span>检查系统条件、安装命令、组件状态和控制平面连接。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/troubleshooting/protection">
    <small>数据保护</small>
    <strong>备份、仓库或恢复失败</strong>
    <span>检查目录权限、对象存储、NAS、Proxy 存储访问、快照和恢复目标。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/troubleshooting/insights">
    <small>智能洞察</small>
    <strong>网关或 Copilot 不可用</strong>
    <span>检查模型、快照范围、公共或私有 Data Gateway 以及数据准备状态。</span>
  </a>
</div>

## 提交问题前

确认问题可以复现后，先在 [GitHub Issues](https://github.com/oneprolabs/hyperfilelens/issues) 搜索已有记录。提交新问题时请说明版本、操作系统、复现步骤和完整错误信息；仅在排查需要时附上已经脱敏的相关日志。
