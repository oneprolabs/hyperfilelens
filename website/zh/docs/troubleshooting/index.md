---
title: 常见问题与故障排查
description: 按失败阶段定位 HyperFileLens 安装、节点、备份、恢复和智能洞察问题。
---

# 常见问题与故障排查

先定位失败发生在哪一段链路，再修改配置。一次同时修改网络、凭据、路径和策略，会让问题更难复现。

## 排障顺序

1. 记录产品版本、时间、页面、资源名称和错误编号。
2. 确认浏览器与控制平面连接正常。
3. 确认相关 Agent、Proxy 或 Data Gateway 在线。
4. 确认源路径和目标仓库分别可访问。
5. 查看任务或验证流程返回的具体失败阶段。
6. 每次只修改一个条件并重新验证。

## 按场景进入

<div class="hfl-doc-grid">
  <a class="hfl-doc-card" href="/zh/docs/troubleshooting/installation-nodes">
    <small>安装与连接</small>
    <strong>安装失败或节点离线</strong>
    <span>检查平台、Docker、注册命令、时间、TLS 和控制平面连接。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/troubleshooting/protection">
    <small>数据保护</small>
    <strong>备份、仓库或恢复失败</strong>
    <span>检查目录权限、对象存储、NAS、Repository Server、快照和恢复目标。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/troubleshooting/insights">
    <small>智能洞察</small>
    <strong>网关或 Copilot 不可用</strong>
    <span>检查模型、快照范围、公共或私有 Data Gateway 以及数据准备状态。</span>
  </a>
  <a class="hfl-doc-card" href="/zh/docs/troubleshooting/account-sign-in">
    <small>访问控制</small>
    <strong>无法访问或登录</strong>
    <span>确认控制台地址、证书、账户状态、密码和语言包。</span>
  </a>
</div>

## 提交问题前

可公开提供：版本、操作系统、错误编号、失败阶段和已脱敏日志。不要提供密码、访问令牌、对象存储密钥、完整 `.env`、客户文件名或可访问的私有地址。

确认问题可复现后，可前往 [GitHub Issues](https://github.com/oneprolabs/hyperfilelens/issues) 搜索已有记录或提交新问题。

