---
title: 升级、备份与回退
description: 使用 Release 安装程序管理 HyperFileLens 的运行状态、受管备份和升级。
---

# 升级、备份与回退

控制平面的安装、备份和升级应通过 `/opt/hyperfilelens/install.sh` 管理。不要直接替换运行目录中的 Compose 文件或镜像标签。

## 日常状态检查

```bash
sudo /opt/hyperfilelens/install.sh status
```

需要重启完整运行栈时：

```bash
sudo /opt/hyperfilelens/install.sh restart
```

## 创建受管备份

在重要配置变更或维护前执行：

```bash
sudo /opt/hyperfilelens/install.sh backup
```

安装程序会创建并验证一个受管备份集，并保留最近的有效备份。该备份保护控制平面运行数据，不等同于业务备份任务中的文件快照。

## 升级

使用已验证的新 Release 包：

```bash
sudo /opt/hyperfilelens/install.sh upgrade \
  --from /path/to/hyperfilelens-<version>.tar.gz
```

升级会先创建受管备份，再执行迁移、启动待切换服务并完成健康检查。不要在升级过程中手工停止容器或删除 `upgrade_tmp`。

在线安装的 Community 环境也可以重新运行与新标签对应的在线安装命令；安装程序会识别现有 Community 安装并进入升级流程。

## 回退原则

只有在目标版本明确支持回退，且受管备份与当前安装身份匹配时才执行回退。数据库迁移可能不可逆，不能仅通过切换旧镜像完成安全回退。

发生升级失败时：

1. 保留安装程序日志和受管备份。
2. 运行 `status` 确认当前活动版本和容器状态。
3. 不要删除 `/opt/hyperfilelens/backup` 或 `.env`。
4. 根据该 Release 的说明恢复，必要时在 GitHub Issue 中提供脱敏日志和错误编号。

