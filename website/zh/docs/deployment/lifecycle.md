---
title: 升级与恢复
description: 使用安装程序检查、备份和升级 HyperFileLens Community，并处理升级异常。
---

# 升级与恢复

本页适用于自行部署的 HyperFileLens Community。官方 SaaS 的升级由 OnePro Cloud 负责，无需用户操作。

Community 的状态检查、系统备份和升级均通过安装程序完成。不要直接替换安装目录中的运行文件或容器镜像。

## 升级前准备

1. 查看目标版本的发布说明，确认当前版本满足升级要求。
2. 选择维护时间，并等待正在运行的备份、恢复和维护任务结束。
3. 确认控制平面服务正常，再开始升级。

检查当前版本和服务状态：

```bash
sudo /opt/hyperfilelens/install.sh status
```

升级程序会在修改系统前自动创建并验证系统备份。如果需要在其他重要维护前单独创建备份，可以运行：

```bash
sudo /opt/hyperfilelens/install.sh backup
```

安装程序保留最近三个有效系统备份。该备份用于恢复 Community 控制平面的配置和运行数据，不包含备份源中的业务文件，也不能代替产品中的备份和恢复验证。

## 执行升级

### 在线升级

使用中国大陆下载源时，运行：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn --tag vX.Y.Z
```

将 `vX.Y.Z` 替换为目标版本号。安装程序会识别现有 Community 环境，并进入升级流程。

### 使用 Release 包升级

已经取得目标版本的 Community Release 包时，使用其完整路径运行：

```bash
sudo /opt/hyperfilelens/install.sh upgrade \
  --from /path/to/hyperfilelens-vX.Y.Z.tar.gz
```

安装程序会验证 Release 包和系统备份，然后完成升级与服务检查。升级过程中不要关闭安装终端、手工停止服务或修改安装目录。

## 验证升级结果

升级完成后：

1. 再次运行 `status`，确认当前版本和服务状态正常。
2. 登录控制台，确认主要页面可以打开。
3. 确认 Agent、Proxy 和 Data Gateway 状态正常。
4. 运行一次代表性的备份或恢复任务，确认任务可以完成。

## 处理升级异常

升级失败时，安装程序会尝试恢复升级前的服务，但不会自动恢复数据库备份。当前版本不支持通过安装旧 Release 包直接降级，也不能仅通过切换旧镜像完成安全恢复。

发生异常后：

1. 不要重复升级、删除系统备份或手工替换运行文件。
2. 运行 `status`，记录当前版本和异常服务。
3. 保留安装程序输出的日志文件和系统备份。
4. 按目标版本发布说明中的恢复要求处理；没有明确恢复步骤时，进入[安装与节点](/zh/docs/troubleshooting/installation-nodes)排查。
