---
title: 安装后检查
description: 确认 HyperFileLens 社区版安装完成并可以开始使用。
---

# 安装后检查

安装完成后，先确认服务正常，再登录控制台检查主要页面。

## 1. 确认服务状态

在安装社区版的主机上运行：

```bash
sudo /opt/hyperfilelens/install.sh status
```

确认输出中的服务状态正常，并记录版本信息。服务异常时，请进入[安装与节点](/zh/docs/troubleshooting/installation-nodes)排查。

## 2. 登录控制台

1. 在安装结果的 `Access` 区域找到 `HyperFileLens`，并在浏览器中打开其 `URL`。
2. 使用同一区域显示的 `Email` 和 `Password` 登录。
3. 首次登录后修改密码。

## 3. 确认产品页面

登录后确认顶部导航栏显示以下入口，并依次打开检查：

- **概览**：查看备份源、目标存储、恢复演练、运行任务和待处理异常等运行概况。
- **数据保护**：进入备份源、目标存储、备份配置和恢复页面。
- **洞察**：打开智能洞察页面。
- **配置管理**：社区版进入<span class="hfl-path">治理 → 用量</span>。组织、成员和角色管理需要企业版治理能力及相应权限；社区版没有这些菜单不代表安装失败。完整说明见[治理](/zh/docs/governance/)。
- **运维监控**：进入运行状态、告警和任务页面；新组织会带有[默认告警规则](/zh/docs/deployment/operations#开箱默认告警规则)。

如果服务或页面无法打开，请记录页面提示和版本信息，并进入[安装与节点](/zh/docs/troubleshooting/installation-nodes)排查。
