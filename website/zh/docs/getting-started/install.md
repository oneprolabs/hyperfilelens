---
title: 安装 Community
description: 使用公开在线安装方式部署最新 HyperFileLens Community Tag。
---

# 安装 Community

如果希望在自己的环境中运行 HyperFileLens，请使用下面的在线安装方式部署最新 Community Tag。

## 安装前准备

- 一台 Ubuntu 20.04、22.04 或 24.04 amd64 主机。
- 已安装且可以正常使用的 Docker Engine 和 Docker Compose V2。
- 能够访问 Gitee、镜像仓库和产品运行所需的网络地址。
- 具备 `sudo` 权限，并为 `/opt/hyperfilelens` 和容器数据预留足够空间。

安装程序会通过 Ubuntu 软件源补齐 Python、rsync、tar、OpenSSL 和 CA
证书等少量运行所需工具；Docker Engine 和 Compose V2 需要预先安装。

详细要求请查看[系统要求](/zh/docs/deployment/requirements)。

## 执行安装

在准备好的主机上运行：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn
```

安装程序从 Gitee 读取最新的语义化版本 Tag，并使用阿里云公共镜像。执行安装或升级前，会显示实际版本、下载来源和镜像仓库并等待确认；正式修改产品安装前还会校验该版本的镜像、资产和代码提交是否完整一致。

如需安装一个已经发布的固定版本，可显式指定：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/main/deploy/online/install.sh \
  | sudo bash -s -- --mirror cn --tag vX.Y.Z
```

自动化场景可以额外传入 `--yes` 跳过确认；普通交互安装不建议使用。
如果最新 Tag 不完整，或指定的 Tag 不存在、源码或镜像不可用，安装程序会列出最多
10 个最近可用的 Tag，并提示正确的 `--mirror ... --tag vX.Y.Z` 用法。程序不会自动降级，
由用户明确选择需要重试的版本。

## 确认安装结果

安装结束后，终端会显示控制台地址和初始登录信息。运行下面的命令确认服务状态：

```bash
sudo /opt/hyperfilelens/install.sh status
```

确认服务正常后，在浏览器打开安装程序输出的控制台地址，继续[登录控制台](/zh/docs/getting-started/sign-in)。

::: warning 保护敏感信息
不要在截图、聊天或公开 Issue 中提供初始密码、`.env`、访问令牌、客户地址或完整安装日志。排障时只提供版本、错误编号和已经脱敏的必要日志片段。
:::
