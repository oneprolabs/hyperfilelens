---
title: AWS S3 接入
description: 为 Personal Edition 创建专用 IAM 用户、精确信任的角色与前缀级 S3 权限，完成托管连接配置与验证。
---

# AWS S3 接入

::: info 适用范围
本指南面向 Personal Edition 运营方，需要企业版支持，并由个人版组织中具有 Managed Storage 管理权限的人员操作。个人版普通用户无需配置云存储身份或密钥。
:::

<p class="hfl-doc-lead">控制端使用专用 IAM 用户的 AK/SK 调用 AWS STS AssumeRole；S3 权限放在角色上，临时会话再限制到具体仓库。</p>

## 开始前准备

- 使用能创建 Bucket、IAM 用户、策略和角色的管理员完成准备；不把这些管理权限授予连接身份。
- 预先创建私有 S3 Bucket，启用 Block Public Access，建议专用于 HyperFileLens。示例为 `hfl-backups-prod`、Object Prefix `personal/`。
- 本页以标准 AWS 分区、同账号用户和角色为例。中国区、GovCloud 或跨账号接入应先核对当前产品目录、ARN 分区、会话策略及 Bucket Policy 支持，不能仅替换域名就视为兼容。
- 如启用 SSE-KMS、Object Lock 或归档存储，单独评估所需权限及仓库生命周期；此示例不包含 KMS 或绕过保留期权限。

## 创建 IAM 用户和精确信任角色

1. 打开 **IAM → Users → Create user**，创建 `hfl-sts-controller`，不启用 AWS Management Console 登录，不添加高权限用户组。
2. 在 **Security credentials → Access keys → Create access key** 创建程序访问密钥，按实际运行环境选择用途。当前连接表单使用显式 AK/SK；控制端部署在 AWS 之外时，同样不得使用 root 密钥。
3. 打开 **IAM → Roles → Create role → Custom trust policy**，创建 `hfl-managed-storage`，使用下列信任策略。将 `<ACCOUNT_ID>` 替换为调用用户所在账号的 12 位 ID，用户名有路径时复制完整 ARN。
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::<ACCOUNT_ID>:user/hfl-sts-controller"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
```

参考 [IAM 用户 AccessKey](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html)与[创建自定义信任角色](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-custom.html)。不要把这里改成 ECS/EC2 等服务主体，也不要只信任整个账号而未限制调用用户。

## 给用户绑定指定角色调用策略

打开 **IAM → Policies → Create policy → JSON**，创建 `hfl-assume-managed-storage`，再在 **Users → hfl-sts-controller → Permissions → Add permissions** 绑定。

将 `<ACCOUNT_ID>` 替换为角色所属账号 ID，并按角色真实路径修改 Resource：
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "sts:AssumeRole",
      "Resource": "arn:aws:iam::<ACCOUNT_ID>:role/hfl-managed-storage"
    }
  ]
}
```

**这份策略绑定给用户，不包含直接 S3 权限。** 角色的信任策略不能代替调用方授权。当前表单没有 ExternalId、MFA 或源身份字段；若安全策略要求这些条件，先适配签发流程，而不是把限制删除。

## 给角色绑定 S3 前缀策略

在 **IAM → Policies → Create policy → JSON** 创建 `hfl-managed-storage-s3`，然后进入 **Roles → hfl-managed-storage → Permissions → Add permissions** 绑定。替换 Bucket 与前缀：
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:ListBucketVersions"
      ],
      "Resource": [
        "arn:aws:s3:::hfl-backups-prod"
      ],
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "personal/repositories/*"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetBucketLocation"
      ],
      "Resource": [
        "arn:aws:s3:::hfl-backups-prod"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:AbortMultipartUpload",
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
        "s3:GetObject",
        "s3:ListMultipartUploadParts",
        "s3:PutObject"
      ],
      "Resource": [
        "arn:aws:s3:::hfl-backups-prod/personal/repositories/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucketMultipartUploads"
      ],
      "Resource": [
        "arn:aws:s3:::hfl-backups-prod"
      ]
    }
  ]
}
```
该策略定义连接级权限上限，覆盖备份、恢复、维护和物理清理。系统签发临时凭据时，进一步按单个仓库和操作类型收窄权限。

- **写入与清理**：`DeleteObject` 用于临时对象清理和仓库维护，因此托管仓库不属于只追加写入存储。版本删除和终止分片上传用于最终清理。
- **Bucket 级元数据**：`s3:ListBucketMultipartUploads` 是当前控制端清理会话使用的指定 Bucket 元数据权限，不带普通对象列举的前缀条件；桶信息查询也属于桶级权限。因此建议使用专用 Bucket；权限范围同时包含前缀级对象权限与上述 Bucket 级元数据权限。
- **对象访问范围**：对象内容的读取、写入、删除，以及对象／版本列举，均受策略中的前缀限制。连接身份不应获得 Bucket 创建、删除、策略管理或全局管理员权限。

该策略不包括 `s3:ListAllMyBuckets`、Bucket 创建删除、Bucket Policy 或 IAM 管理权限。`s3:prefix` 条件应放在列举语句，不放到 `GetBucketLocation` 或对象内容语句。权限、信任和会话交集规则见 [AssumeRole](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html)。

## 填写 Add Object Storage

选择 **AWS**，再选择 Bucket 真实地域：

| 页面字段 | 内容与获取位置 |
| --- | --- |
| Endpoint URL | 选区目录提供的 S3 地域 Endpoint，例如 `s3.us-east-1.amazonaws.com`；不是带 Bucket 的访问域名或网站 Endpoint |
| Region | 在 S3 Bucket 的属性中确认，例如 `us-east-1`，必须与 Endpoint 和签名地域一致 |
| Access Key / Secret Key | 专用 IAM 用户的 Access key ID / Secret access key，首次创建时安全保存 |
| S3 URL Style | 保留对应地域目录默认值；手动选择 Virtual Hosted Style 时确认 Bucket DNS 名与 TLS 匹配 |
| Use TLS (HTTPS) | 开启 |
| Identity Service Endpoint | 可使用当前默认 `https://sts.amazonaws.com`；需地域化时使用实际 AWS STS Endpoint，例如 `https://sts.us-east-1.amazonaws.com`，按 IAM/网络要求确认 |
| Role ARN | **IAM → Roles → hfl-managed-storage → Summary → ARN**，完整复制，不填用户 ARN |

角色的最大会话时长可保持正常的 1 小时设置；HyperFileLens 当前默认请求 15 分钟，不需要长时间任务而延长单次云 Token 的有效期。参考 [AWS STS 地域](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_temp_enable-regions.html)。

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
| AssumeRole AccessDenied | 用户调用策略的角色 ARN、角色信任中的用户 ARN、组织 SCP／权限边界和额外条件 |
| 只读校验 AccessDenied | 角色 S3 策略、Bucket Policy、`s3:prefix`；策略允许的是 `personal/repositories/*`，不是只允许 `personal/` 根目录列举 |
| Endpoint／Region 不匹配 | 必须修正真实地域；预检不会通过 SDK 自动跳转修正地域配置 |
| 写入或删除失败 | 维护所需 DeleteObject、Multipart、版本权限，以及 KMS、Object Lock、保留策略是否另有限制 |

## 配置核对清单

完成存储端配置后，核对以下项目，再在 **Add Object Storage** 中填写连接信息。

- 角色信任策略仅允许专用 IAM 用户；指定角色的 sts:AssumeRole 调用策略绑定给该用户。
- S3 数据策略绑定给角色，保留备份、恢复、维护及清理所需权限，并限定对象范围。
- 未使用 root 密钥，未授予 AdministratorAccess、AmazonS3FullAccess 或 IAM 管理权限。
- Bucket 信息和未完成分片上传列表权限为指定 Bucket 级别；对象内容访问和对象／版本列举仍受仓库前缀限制。

### 所需配置信息

| 配置项 | 核对要求 |
| --- | --- |
| Bucket | 已创建且启用 Block Public Access 的专用私有 S3 Bucket 名称 |
| Region / Endpoint URL | Bucket 的实际地域及对应 S3 Endpoint |
| Role ARN | 从 IAM 角色详情复制的完整 ARN |
| Identity Service Endpoint | 与实际账号及网络要求匹配的 AWS STS Endpoint |
| Access Key / Secret Key | 专用 IAM 用户的 Access key ID / Secret access key，通过安全渠道配置 |
| 额外安全配置 | KMS、Object Lock、SCP、权限边界或特殊 Bucket Policy 的适用情况 |
| Object Prefix | 默认建议 `personal/`；须与本页策略中的仓库前缀一致 |

密钥仅通过安全渠道配置到控制端，不写入普通工单、文档或代码仓库。无需手动创建临时 Token，也无需提供云平台主账号密码。

## 官方参考

- [IAM AccessKey 管理](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html)
- [创建自定义信任角色](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_create_for-custom.html)
- [STS AssumeRole](https://docs.aws.amazon.com/STS/latest/APIReference/API_AssumeRole.html)
- [S3 权限与条件键](https://docs.aws.amazon.com/service-authorization/latest/reference/list_s3.html)
- [S3 Block Public Access](https://docs.aws.amazon.com/AmazonS3/latest/userguide/access-control-block-public-access.html)
