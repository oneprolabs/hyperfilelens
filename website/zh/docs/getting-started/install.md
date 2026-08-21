---
title: 安装 HyperFileLens
description: 使用公开在线安装程序或离线 Release 包安装 HyperFileLens Community。
---

# 安装 HyperFileLens

HyperFileLens Community 支持在线安装和完整离线 Release 包安装。生产环境应固定具体版本，不要直接使用未发布分支或浮动标签。

## 在线安装

在线安装适合已安装 Docker Engine 和 Compose V2 的 Ubuntu 20.04、22.04 或 24.04 amd64 主机。将示例中的 `vX.Y.Z` 替换为准备安装的正式 Release 标签。

海外环境使用 GitHub 和 Docker Hub：

```bash
curl -fsSL https://raw.githubusercontent.com/oneprolabs/hyperfilelens/vX.Y.Z/deploy/online/install.sh \
  | sudo bash -s -- vX.Y.Z --region global --download-source github --yes
```

中国大陆环境使用 Gitee 和阿里云镜像：

```bash
curl -fsSL https://gitee.com/oneprolabs/hyperfilelens/raw/vX.Y.Z/deploy/online/install.sh \
  | sudo bash -s -- vX.Y.Z --region cn --download-source gitee --yes
```

`--region` 选择 Docker 镜像区域；`--download-source` 选择安装文件来源。使用 `auto` 时，中国区优先 Gitee，其他地区优先 GitHub，失败后自动尝试另一个公开源。Gitee 仓库和镜像均为公开资源，不需要账号或 Token。`--yes` 表示非交互执行，适合脚本和自动升级。

安装程序会检查系统、Docker 和 Compose，准备与该标签一致的安装内容，并将运行目录写入 `/opt/hyperfilelens`。它只安装或升级 Community，不会把 Enterprise 安装转换为 Community。

## 离线安装

在联网环境下载对应版本的完整 Release 包并完成校验，然后传输到目标主机：

```bash
tar xzf hyperfilelens-<version>.tar.gz
cd hyperfilelens-<version>
sudo ./install.sh install
```

离线包包含应用容器镜像、Agent 安装介质以及受支持 Ubuntu 版本的 Docker 安装包。已有健康且满足版本要求的 Docker 会被复用；不满足安全前置条件时，安装程序会停止并给出原因。

## 安装后检查

安装结束后，终端会输出控制台地址和初始登录信息。先执行：

```bash
sudo /opt/hyperfilelens/install.sh status
```

确认服务状态正常后，在浏览器打开租户控制台。默认 TLS 证书仅覆盖安装包声明的本地测试名称；生产访问应配置与实际域名匹配的证书并建立正确的客户端信任。

## 启用中文界面

如果当前 Release 已包含简体中文语言包，可安装并列出语言包：

```bash
sudo /opt/hyperfilelens/install.sh lang-pack install --id zh-hans
sudo /opt/hyperfilelens/install.sh lang-pack list
```

安装完成后刷新浏览器，并从界面语言入口选择简体中文。语言包必须与当前产品版本兼容。

## 首次登录后的操作

1. 使用安装程序输出的初始账户登录。
2. 立即修改公开默认密码。
3. 核对系统时间、浏览器访问地址和证书。
4. 进入[完成首次备份](/zh/docs/getting-started/first-backup)，部署第一个备份源。

::: warning 不要跳过
不要把安装终端输出、`.env`、访问令牌或客户环境地址复制到公开 Issue 和截图中。提交问题时仅提供已脱敏的错误编号、版本和必要日志片段。
:::
