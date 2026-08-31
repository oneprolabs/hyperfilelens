---
title: 添加目标存储
description: 在备份向导中添加并验证本次示例使用的对象存储。
---

# 添加目标存储

目标存储用于保存备份快照。本章使用 Huawei Cloud 对象存储，并从备份配置的 **Target** 步骤开始添加。

## 准备连接信息

提前准备 Huawei Cloud OBS 的 Endpoint、Region、Bucket、Object Prefix、Access Key 和 Secret Key。使用专用于 HyperFileLens 的 Bucket 或 Object Prefix，不要与其他备份仓库或业务文件混用。

### 准备 IAM 用户和访问密钥

1. 登录 [Huawei Cloud 控制台](https://auth.huaweicloud.com/authui/login.html)。
2. 创建一个仅用于本次验证的 IAM 用户，并将访问方式设置为 **Programmatic access**。
3. 为该 IAM 用户授予完成本次流程所需的 OBS API 权限。HyperFileLens 至少需要查询 Bucket；选择 **Create New Bucket** 时还需要创建 Bucket；初始化和使用仓库还需要对指定 Bucket 或 Object Prefix 执行所需对象操作。
4. 创建 Access Key，通过邮件、手机或虚拟 MFA 完成验证，然后下载密钥文件。也可以从账户菜单进入 **My Credentials → Access Keys** 管理访问密钥。

**Management console access** 只允许登录 Huawei Cloud 控制台，不会自动授予 OBS API 权限。如果 HyperFileLens 提示凭据无法列出 Bucket，请检查该 IAM 用户的 OBS 策略及其作用范围，等待权限生效后重试。

Access Key 和 Secret Key 属于敏感信息。Secret Key 通常只在创建时显示一次；不要将密钥文件、AK/SK 或填写后的原始截图上传到聊天、Issue 或 Git 仓库。

## 从备份配置添加仓库

进入 **Target** 步骤后，页面会显示当前 Windows 源端尚未分配目标仓库。

1. 选择 **Add Repository**。系统会在新的浏览器标签页中打开目标存储页面。

![Target 步骤提示尚未分配目标仓库，Windows 主机信息已经模糊处理](/docs/getting-started/add-target-repository.png)

2. 在 **Object Storage** 页面选择 **Add**。

![尚未添加对象存储仓库的 Object Storage 页面，账户已经模糊处理](/docs/getting-started/empty-object-storage.png)

3. 在 **Add Object Storage Repository** 页面选择 **Huawei Cloud**。
4. 选择 OBS Bucket 所在的 **Region**，并核对自动填充的 **Endpoint URL** 和 **Region**。

![添加 Huawei Cloud 对象存储仓库并选择 Region，账户已经模糊处理](/docs/getting-started/select-huawei-cloud.png)

5. 输入 IAM 用户的 **Access Key** 和 **Secret Key**。
6. 保持 **Use TLS (HTTPS)** 开启，并确认右侧摘要显示 **HTTPS**。Huawei Cloud OBS 公网连接不要使用 HTTP。
7. 已有专用 Bucket 时选择 **Select Existing Bucket**；否则选择 **Create New Bucket** 并输入全局唯一的 Bucket 名称。
8. 输入专用于本仓库的 **Object Prefix**，例如 `hfl/`，并核对仓库名称、Bucket 和其他设置。
9. 选择 **Create and Initialize Repository**。

![Huawei Cloud 仓库配置，Access Key、Bucket 和仓库名称已经模糊处理](/docs/getting-started/configure-huawei-repository.png)

如果页面提示凭据无法列出 Bucket，不要关闭 TLS 或改用主账号密钥绕过。为测试 IAM 用户补齐所需 OBS API 权限，确认策略作用于正确账号或项目并等待权限生效，然后重新验证。

选择 **Create and Initialize Repository** 后，页面会返回 **Object Storage** 列表。等待新仓库显示 **Status: Created** 且 **Connectivity: Online**，表示目标仓库已经可以使用。

![Huawei Cloud 仓库已创建并在线，账户、仓库名和 Bucket 已经模糊处理，Endpoint 和注册时间保持可见](/docs/getting-started/huawei-repository-created.png)

本次实测已确认仓库创建成功并回到列表页面。将该仓库分配回备份配置、完成后续确认步骤后，再继续[创建并运行首次备份](/zh/docs/getting-started/first-backup)。
