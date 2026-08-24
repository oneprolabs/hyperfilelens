---
title: 安装 Community
description: 使用当前公开安装方式部署 HyperFileLens Community v0.2.8。
---

# 安装 Community

如果希望在自己的环境中运行 HyperFileLens，请安装 Community。当前公开版本为 `v0.2.8`，使用下面的在线安装方式。

## 安装前准备

- 一台 Ubuntu 20.04、22.04 或 24.04 amd64 主机。
- 已安装且可以正常使用的 Docker Engine 和 Docker Compose V2。
- 能够访问 Gitee、镜像仓库和产品运行所需的网络地址。
- 具备 `sudo` 权限，并为 `/opt/hyperfilelens` 和容器数据预留足够空间。

详细要求请查看[系统要求](/zh/docs/deployment/requirements)。

## 执行安装

在准备好的主机上运行：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/v0.2.8/deploy/online/install.sh \
  | sudo bash -s -- v0.2.8 --region cn --download-source gitee --yes
```

该命令从 Gitee 获取 `v0.2.8` 安装内容，并使用中国大陆镜像来源部署 Community。不要把版本替换为未发布分支或浮动标签。

## 确认安装结果

安装结束后，终端会显示控制台地址和初始登录信息。运行下面的命令确认服务状态：

```bash
sudo /opt/hyperfilelens/install.sh status
```

确认服务正常后，在浏览器打开安装程序输出的控制台地址，继续[登录与初始设置](/zh/docs/getting-started/sign-in)。

::: warning 保护敏感信息
不要在截图、聊天或公开 Issue 中提供初始密码、`.env`、访问令牌、客户地址或完整安装日志。排障时只提供版本、错误编号和已经脱敏的必要日志片段。
:::
