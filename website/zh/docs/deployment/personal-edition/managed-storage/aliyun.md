---
title: 阿里云 OSS 接入
description: 创建最小权限 RAM 用户与角色，为 Personal Edition 配置阿里云托管存储，并完成连接配置与验证。
---

# 阿里云 OSS 接入

::: info 适用范围
本指南面向 Personal Edition 运营方，需要企业版支持，并由个人版组织中具有 Managed Storage 管理权限的人员操作。个人版普通用户无需配置云存储身份或密钥。
:::

<p class="hfl-doc-lead">使用专用 RAM 用户的长期 AK/SK 调用阿里云 STS，扮演限定 OSS 范围的角色。客户端只收到系统按仓库前缀收窄的临时凭据。</p>

## 开始前准备

- 由能管理 OSS Bucket、RAM 用户、角色和自定义权限策略的管理员完成云端准备；这些管理权限不交给 HyperFileLens。
- 准备已存在的私有 Bucket。以下示例使用 `hfl-backups-prod`、Object Prefix `personal/`，实际名称可修改。
- 本页以同账号 RAM 用户与角色为例；跨账号时分别使用调用用户账号与角色所属账号的真实 ARN，并检查 Bucket Policy。
- 确认控制端可访问阿里云 STS，所有执行组件可访问 Bucket 对应外网 Endpoint。不要默认使用 OSS 内网地址。

## 创建专用 RAM 用户

1. 打开 **访问控制 RAM → 身份管理 → 用户 → 创建用户**，创建 `hfl-sts-controller`，只供程序调用，不需要控制台登录。
2. 在该用户的 **AccessKey** 页签创建 AccessKey，安全保存 AccessKey ID 和 AccessKey Secret。不要创建主账号 AccessKey。
3. 记录实际账号 UID。Role ARN 应从角色详情复制。阿里云账号 UID 通常为 16 位，须保留完整值，不可按 AWS 示例截断为 12 位。
4. 不加入高权限用户组，不绑定 `AdministratorAccess`、`AliyunOSSFullAccess` 或允许扮演所有角色的 `AliyunSTSAssumeRoleAccess`。

操作参考：[创建 RAM 用户](https://help.aliyun.com/zh/ram/user-guide/create-a-ram-user)、[创建和使用 AccessKey](https://help.aliyun.com/zh/ram/user-guide/create-an-accesskey-pair)。

## 创建角色并设置精确信任

1. 打开 **RAM → 身份管理 → 角色 → 创建角色**，选择**云账号／阿里云账号**作为可信实体，创建 `hfl-managed-storage`。不要选择 ECS 等云服务角色。
2. 在角色详情的**信任策略**中使用下列内容。将 `<ACCOUNT_ID>` 替换为调用用户所属账号 UID；如果用户名包含路径，直接复制完整用户 ARN。
3. 只信任指定用户，不保留整账号 `root` 的宽泛信任。界面名称变化时按[指定用户扮演指定角色](https://help.aliyun.com/zh/ram/support/faq-about-ram-roles-and-sts-tokens)核对。
```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "RAM": [
          "acs:ram::<ACCOUNT_ID>:user/hfl-sts-controller"
        ]
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

## 给用户授予指定角色调用权限

进入 **RAM → 权限管理 → 权限策略 → 创建权限策略 → 脚本编辑**，创建 `hfl-assume-managed-storage`。将 `<ACCOUNT_ID>` 替换为目标角色所属账号 UID，角色存在路径时复制完整角色 ARN。
```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "acs:ram::<ACCOUNT_ID>:role/hfl-managed-storage"
    }
  ]
}
```

回到 **用户 → hfl-sts-controller → 添加权限**，仅绑定这个调用策略。**这一份绑定给用户，不是角色；用户不直接获得 OSS 数据权限。** 账号 UID 不同的跨账号接入不能复用同一个占位符值。

## 给角色授予 OSS 前缀权限

通过同一入口创建自定义策略 `hfl-managed-storage-oss`，再进入 **角色 → hfl-managed-storage → 添加权限** 绑定。**这一份绑定给角色，不是长期 AK/SK 所属用户。**

替换所有 Bucket 名称和前缀后使用：
```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "oss:ListObjects",
        "oss:ListObjectVersions"
      ],
      "Resource": [
        "acs:oss:*:*:hfl-backups-prod"
      ],
      "Condition": {
        "StringLike": {
          "oss:Prefix": [
            "personal/repositories/",
            "personal/repositories/*"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:GetBucketInfo"
      ],
      "Resource": [
        "acs:oss:*:*:hfl-backups-prod"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:AbortMultipartUpload",
        "oss:DeleteObject",
        "oss:DeleteObjectVersion",
        "oss:GetObject",
        "oss:ListParts",
        "oss:PutObject"
      ],
      "Resource": [
        "acs:oss:*:*:hfl-backups-prod/personal/repositories/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "oss:ListMultipartUploads"
      ],
      "Resource": [
        "acs:oss:*:*:hfl-backups-prod"
      ]
    }
  ]
}
```
该策略定义连接级权限上限，覆盖备份、恢复、维护和物理清理。系统签发临时凭据时，进一步按单个仓库和操作类型收窄权限。

- **写入与清理**：`DeleteObject` 用于临时对象清理和仓库维护，因此托管仓库不属于只追加写入存储。版本删除和终止分片上传用于最终清理。
- **Bucket 级元数据**：`oss:ListMultipartUploads` 是当前控制端清理会话使用的指定 Bucket 元数据权限，不带普通对象列举的前缀条件；桶信息查询也属于桶级权限。因此建议使用专用 Bucket；权限范围同时包含前缀级对象权限与上述 Bucket 级元数据权限。
- **对象访问范围**：对象内容的读取、写入、删除，以及对象／版本列举，均受策略中的前缀限制。连接身份不应获得 Bucket 创建、删除、策略管理或全局管理员权限。

不要用 MFA、ExternalId 或 SourceIdentity 等当前表单没有提供的条件直接替换此机器调用流程。若组织强制要求这些条件，应先适配签发流程，而不是移除公司的安全要求。调用用户授权和角色信任策略都必须匹配，临时会话不能扩大角色权限。参考[扮演 RAM 角色](https://help.aliyun.com/zh/ram/user-guide/assume-a-ram-role)和[OSS 权限策略](https://help.aliyun.com/zh/oss/user-guide/ram-policy)。

## 填写 Add Object Storage

选择 **Alibaba Cloud** 和 Bucket 的实际地域。

| 页面字段 | 内容与获取位置 |
| --- | --- |
| Endpoint URL | 从 OSS Bucket 概览确认地域与外网 Endpoint，在 HyperFileLens 中选择相应目录项；不填带 Bucket 名称的访问域名 |
| Region | 填写 Bucket 的真实地域，例如杭州 `cn-hangzhou`；当前 HyperFileLens 目录可能显示 `oss-cn-hangzhou`，保留目录配套值，不手动混配不同 SDK 的签名地域 |
| Access Key / Secret Key | 专用 RAM 用户的 AccessKey ID / AccessKey Secret，不是角色名或临时 Token |
| S3 URL Style | 选择地域后保留目录默认值，当前阿里云目录为 Virtual Hosted Style |
| Use TLS (HTTPS) | 开启 |
| Identity Service Endpoint | 当前默认 `https://sts.aliyuncs.com`；如需地域化 STS，按官方接入点和网络配置确认 |
| Role ARN | **RAM → 身份管理 → 角色 → hfl-managed-storage → 基本信息 → ARN**，完整复制 `acs:ram::<ACCOUNT_ID>:role/hfl-managed-storage` |

::: warning 区分官方 SDK 示例和当前产品目录
阿里云当前 [AWS SDK 接入文档](https://help.aliyun.com/zh/oss/developer-reference/use-aws-sdks-to-access-oss)使用 `s3.oss-<region>.aliyuncs.com`。当前 HyperFileLens 托管连接目录使用 `oss-<region>.aliyuncs.com`，校验器未接受前述 `s3.oss-` 形式；该 Endpoint 形式目前不受托管连接校验器支持。先使用与部署版本配套的目录项完成签发、初始化和备份恢复测试；如该地域需新的 S3 Endpoint，应先完成产品适配，不应通过扩大权限或使用未经确认的 Region 规避校验。
:::

### 存储配置

| 页面字段 | 填写内容 |
| --- | --- |
| Name | 系统根据平台和 Bucket 自动生成，也可以修改；它不是云平台的用户名或角色名 |
| Bucket | 实际已存在的私有 Bucket 名称；`hfl-backups-prod` 只是建议名称 |
| Object Prefix | 新连接建议为 `personal/`；系统自动追加 `repositories/<repository_uuid>/`，不要手动填某个用户或仓库 UUID |

如果使用其他 Bucket 或连接前缀，必须同步替换本页所有策略资源和前缀条件。示例策略中的 `*` 表示该连接下的仓库，不代表可以访问其他 Bucket。不要为了编辑名称或轮换密钥而改变已有仓库的位置。

## 验证连接与仓库

::: warning 首次配置与个人注册
首次配置期间应保持个人注册关闭，使用已有测试成员验证，或在受控测试环境完成验证。新连接默认启用；没有默认连接时，新连接保存后会自动设为默认。新连接无需单独激活，页面不提供 Disable 操作。完成初始化、备份和恢复测试并确认生产默认连接后，再开放个人注册。
:::

如需恢复已有的 Disabled 连接，选择该连接后使用 **More Actions → Enable**，需要连接管理权限。系统在启用前重新检查临时凭据签发和只读访问；检查失败时，连接保持停用状态，不变更现有默认连接。启用成功后，已有默认连接保持不变；没有默认连接时，恢复的连接会自动成为默认。恢复操作同样应遵循本页的验证流程，在验证完成并确认生产默认连接后再开放个人注册。

1. 切换到 **Personal Edition** 组织，打开 **Managed Storage → Object Storage → Add**，完成上述字段后选择 **Add**。页面名称以当前界面语言为准。
2. 保存前系统尝试签发仓库范围的只读临时凭据，并列举该前缀下至多一个对象；空 Bucket 不需要预先创建目录。校验失败先检查本页的身份、策略和网络，不要改用全权密钥。
3. 核对连接的真实 Enabled 状态和 Default 标签。已有默认连接时，新增连接不会替换它；新连接验证通过后，如需承接后续个人注册，再使用 **More Actions → Set as Default** 切换。更换默认连接只影响后续创建，不会迁移已有仓库。
4. 在 **Backup Repositories** 创建一个测试仓库并选择测试成员为 Owner；需要相应的仓库创建及 Owner 分配权限。历史成员补建使用 **More Actions → Provision Missing Repositories**；已有失败仓库应使用 **Retry Initialization**，而不是反复补建。
5. 等待仓库初始化成功，再由测试成员通过 **Backup Wizard** 完成小文件备份和恢复。检查该成员不能访问其他成员的仓库。需要验证删除清理时，只使用可丢弃测试仓库并确认删除范围。

**连接保存成功只代表签发和只读访问检查通过，不等于已经验证写入、完整仓库生命周期或所有客户端网络。** 完整验收步骤见[托管存储概述](./index#验证与上线)。

## 常见错误

| 问题 | 检查 |
| --- | --- |
| 临时凭据签发失败 | 用户是否有指定角色的 `sts:AssumeRole`；角色是否精确信任该用户；ARN、AK/SK 和账号 UID 是否正确 |
| 保存或初始化 AccessDenied | OSS 策略是否绑定在角色；Bucket 和 `oss:Prefix` 是否匹配；是否有 Bucket Policy 显式拒绝或网络条件 |
| Region／签名错误 | 按上面的兼容性提示检查目录 Endpoint 和签名 Region，不能把 STS 地域当作 Bucket 地域 |
| 备份成功但删除清理失败 | 检查版本列举、版本删除和 Multipart 权限；排查过程不得删除生产 Bucket 中的数据 |

## 配置核对清单

完成存储端配置后，核对以下项目，再在 **Add Object Storage** 中填写连接信息。

- 角色信任策略仅允许专用 RAM 用户；该用户的调用策略仅允许扮演指定角色。
- OSS 数据策略绑定给角色，不直接授予长期 AK/SK 所属用户；对象范围与连接前缀一致。
- 未授予全局管理员、OSS 全权、任意角色调用或 Bucket 创建／删除权限。
- Bucket 信息和未完成分片上传列表权限为指定 Bucket 级别；对象内容访问和对象／版本列举仍受仓库前缀限制。

### 所需配置信息

| 配置项 | 核对要求 |
| --- | --- |
| Bucket | 已创建的专用私有 Bucket 名称 |
| Region / Endpoint URL | Bucket 的真实地域，以及与当前产品目录配套的外网 OSS Endpoint |
| Role ARN | 从 RAM 角色详情复制的完整 ARN |
| Identity Service Endpoint | 默认 `https://sts.aliyuncs.com`；使用地域化地址时须确认网络与地域配置 |
| Access Key / Secret Key | 专用 RAM 用户的 AccessKey ID / AccessKey Secret，通过安全渠道配置 |
| Object Prefix | 默认建议 `personal/`；须与本页策略中的仓库前缀一致 |

密钥仅通过安全渠道配置到控制端，不写入普通工单、文档或代码仓库。无需手动创建临时 Token，也无需提供云平台主账号密码。

## 官方参考

- [RAM 用户与指定角色授权](https://help.aliyun.com/zh/ram/support/faq-about-ram-roles-and-sts-tokens)
- [自定义权限策略](https://help.aliyun.com/zh/ram/create-a-custom-policy)
- [OSS 权限策略](https://help.aliyun.com/zh/oss/user-guide/ram-policy)
- [OSS Bucket 概览与 Endpoint](https://help.aliyun.com/zh/oss/bucket-overview-page)
- [STS 接入点](https://help.aliyun.com/zh/ram/developer-reference/api-sts-2015-04-01-endpoint)
