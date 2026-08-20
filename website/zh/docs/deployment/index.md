---
title: 部署与节点
description: 理解并部署 HyperFileLens Agent、Proxy 和 Data Gateway。
---

# 部署与节点

HyperFileLens 使用不同节点承担生产数据访问、共享存储接入和智能洞察执行。选择节点角色时，应以它需要访问的数据和网络边界为依据。

## 节点职责

| 角色 | 主要职责 | 典型位置 |
| --- | --- | --- |
| Agent | 读取所在主机的文件，执行备份和恢复 | 需要保护的 Linux、Windows 或 macOS 主机 |
| Proxy | 访问 NAS、提供本地磁盘目标或协助跨节点存储访问 | 能连接共享存储和源端网络的 Linux 主机 |
| Data Gateway | 为 Copilot 准备并访问选定的备份数据 | 能访问备份仓库且满足 AI 引擎要求的隔离主机 |

同一主机可以按部署方式运行相关组件，但每个角色的职责和连通条件仍需分别验证。不要因为主机上已安装 Agent，就假定它自动具备 Proxy 或 Data Gateway 的网络和运行条件。

## 部署原则

- 从产品界面生成当前注册命令，不复用过期截图中的令牌或命令。
- 先确认节点时间、DNS、证书信任和到控制平面的 HTTPS 连接。
- NAS 和本地磁盘使用专用路径，避免与其他应用共用目录。
- Data Gateway 只处理用户明确选定的备份快照和范围，不直接读取生产目录。
- 升级前检查控制平面与节点版本兼容性，并保留安装程序生成的受管备份。

继续阅读：[Agent 与 Proxy](/zh/docs/deployment/nodes)、[Data Gateway](/zh/docs/deployment/data-gateway)和[升级、备份与回退](/zh/docs/deployment/lifecycle)。
