---
title: 卸载社区版
description: 完整卸载 HyperFileLens 社区版，或在保留配置和业务数据的情况下移除运行组件。
---

# 卸载社区版

HyperFileLens 通过已安装的管理程序完成卸载。不要手工删除容器、镜像或安装目录，以免留下不完整的运行状态。

## 卸载前准备

1. 等待正在运行的备份、恢复和洞察任务结束。
2. 确认是否需要保留当前配置、业务数据、系统备份和日志。
3. 确认 Docker daemon 正常运行。

## 选择卸载方式

### 完整卸载

```bash
sudo /opt/hyperfilelens/install.sh uninstall
```

该命令会立即开始卸载，并移除 HyperFileLens 管理的运行组件、配置、业务数据、系统备份、日志和 `/opt/hyperfilelens` 安装目录。该操作不可逆，请先确认需要保留的数据已经安全转移。

### 保留数据

```bash
sudo /opt/hyperfilelens/install.sh uninstall --keep-data
```

该命令会移除运行组件和应用文件，但保留配置、业务数据、系统备份和日志。需要恢复使用时，重新运行社区版在线安装命令。

## 卸载边界

- 卸载不会移除主机上的 Docker CE、Docker Compose 或 containerd。
- 卸载不会移除与 HyperFileLens 无关的容器、镜像或网络。
- 部署在其他主机上的 Agent 和 Proxy 拥有独立的生命周期，不会随控制平面一起卸载。
