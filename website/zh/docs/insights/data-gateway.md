---
title: 使用 Private Data Gateway
description: 选择并使用能够访问备份仓库的 Private Data Gateway。
---

# 使用 Private Data Gateway

当备份仓库位于私有网络，或者不能由平台提供的网关访问时，可以使用部署在可控网络中的 Private Data Gateway。

## 使用前检查

- 网关状态在线且版本兼容。
- 网关能够访问 HyperFileLens 控制平面和所选备份仓库。
- 工作区磁盘空间充足。
- DNS、系统时间和 TLS 信任正确。

## 在会话中选择

创建 AI Copilot 会话时，选择手动指定 Data Gateway，然后从当前可用列表中选择 Private Data Gateway。提交前再次核对快照、文件范围和网关。

需要新部署网关时，请查看[Private Data Gateway 部署](/zh/docs/deployment/data-gateway)。
