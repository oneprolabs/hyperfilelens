---
title: 部署 Private Data Gateway
description: 部署用于 HyperFileLens 智能洞察的 Data Gateway。
---

# 部署 Private Data Gateway

Data Gateway 连接 HyperFileLens 备份仓库与 AI 引擎，用于准备用户在 Copilot 会话中明确选择的数据。它不直接读取生产主机目录。

## 公共与私有 Data Gateway

- **公共 Data Gateway**由平台提供并作为默认选择，适合平台统一维护的运行环境。
- **私有 Data Gateway**由用户或组织部署，适合备份仓库只能从私有网络访问的场景。

Community 环境是否提供可用公共 Data Gateway，取决于实际部署配置。界面没有可用默认网关时，应部署私有 Data Gateway，而不是假设产品会自动访问私有仓库。

## 部署前检查

- Linux/Ubuntu amd64 主机。
- Docker Engine 和 Compose V2 可用，或者允许受支持的离线安装程序安装捆绑版本。
- 能够访问 HyperFileLens 控制平面和所需备份仓库。
- 有足够磁盘空间保存会话工作区和临时数据。
- 时间、DNS 和 TLS 信任正确。

## 部署步骤

1. 打开<span class="hfl-path">洞察 → 数据网关</span>。
2. 选择部署私有 Data Gateway。
3. 在向导中核对系统与资源要求。
4. 复制当前生成的命令，在目标 Linux 主机上运行。
5. 等待 Agent 注册、Docker 检查和 AI 引擎安装完成。
6. 返回控制台，确认网关在线且状态可用。
7. 创建测试 Copilot 会话，验证网关能浏览所选快照目录并完成数据准备。

Data Gateway 的工作区应使用产品管理的专用路径。卸载和清理时只操作界面与安装程序声明的产品目录，不要手工使用宽泛的递归删除命令。
