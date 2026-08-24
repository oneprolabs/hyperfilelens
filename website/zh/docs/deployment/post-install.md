---
title: 安装后检查
description: 检查 HyperFileLens Community 服务、控制台和基础配置。
---

# 安装后检查

安装结束后，先确认控制平面可以稳定运行，再添加正式备份源。

## 检查服务

在控制平面主机运行：

```bash
sudo /opt/hyperfilelens/install.sh status
```

确认安装程序报告的核心服务正常，并记录当前版本。服务异常时不要反复重新安装，应先保留错误信息并进入[安装与节点排障](/zh/docs/troubleshooting/installation-nodes)。

## 检查控制台

1. 使用安装程序输出的地址打开控制台。
2. 使用初始账户登录并立即修改密码。
3. 核对浏览器地址、证书、系统时间和时区。
4. 确认数据保护、智能洞察和租户运维页面可以打开。

## 启用简体中文

如果当前 Release 已包含简体中文语言包，可执行：

```bash
sudo /opt/hyperfilelens/install.sh lang-pack install --id zh-hans
sudo /opt/hyperfilelens/install.sh lang-pack list
```

安装后刷新浏览器并选择简体中文。语言包必须与当前产品版本兼容。

