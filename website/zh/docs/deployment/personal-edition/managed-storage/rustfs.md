---
title: RustFS 接入
description: 为 Personal Edition 配置 RustFS 普通用户、仓库前缀策略和 STS，核对版本兼容性、网络条件和连接参数。
---

# RustFS 接入

::: info 适用范围
本指南面向 Personal Edition 运营方，需要企业版支持，并由个人版组织中具有 Managed Storage 管理权限的人员操作。个人版普通用户无需配置云存储身份或密钥。
:::

<p class="hfl-doc-lead">RustFS 使用自身的 STS 和普通内置用户，不需要创建 AWS 云账号角色。先确认部署能力，再用专用、前缀受限的身份接入。</p>

## 开始前准备

- 由存储管理员准备已存在的私有 Bucket，示例 `hfl-backups-prod`；配置专用连接前缀 `personal/`。
- 提供 **S3 API 地址**，不是管理 Console 地址。应以实际监听和反向代理配置为准，不能仅依据常见的 `9000/9001` 端口判断。
- 控制端和执行组件均须可达存储地址；DNS、证书信任链和防火墙必须一致。当前产品要求 S3 与 STS 使用同一协议、主机和端口，根路径不能带 `/s3` 等代理前缀。
- HTTPS 推荐用于生产。当前版本仅对 localhost、回环或允许的私有 IP 字面量开放 HTTP 例外；域名即使解析到内网，也不自动符合 HTTP 例外条件。不要关闭证书校验。
- RustFS 兼容性取决于实际部署版本和配置。记录并验证实际服务端版本、IAM 配置和 Token 续签；官方文档中列出的其他身份模式不一定受当前集成支持。

## 创建专用用户并绑定策略

1. 记录 RustFS 服务端版本和部署配置，由管理员确认支持**普通 IAM 用户签名的 AssumeRole**、内联 Policy、会话 Token 校验，以及本页列举／版本／Multipart 权限。不要仅凭 S3 上传成功判断托管兼容。
2. 在 RustFS Console 的 **Identity → Policies** 创建自定义策略 `hfl-managed-storage`，粘贴下一节 JSON；若界面标签不同，按所部署版本的 IAM 文档操作。
3. 在 **Identity → Users** 创建已启用的普通 IAM 用户 `hfl-sts-controller`，设置高强度 secret，并附加这个策略。确认没有额外的全权用户组或 `readwrite`、`consoleAdmin` 策略。
4. 连接使用此**普通用户自身**的 access key（用户名）和 secret。不能使用 **Access Keys** 中创建的 Service Account 代替普通用户：官方说明 Service Account 和临时身份不能调用 AssumeRole。
5. RustFS 采用用户策略与会话策略共同约束临时身份；本集成不要求创建 AWS IAM Role，也不需要填写 Role ARN。不要为此添加 `admin:*`。

存储端配置可使用 [RustFS Console / IAM 操作](https://docs.rustfs.com/en/security-compliance/iam/policies)或对应版本的 `rc admin`。RustFS 与 MinIO 的管理命令及 API 不能直接互换。

## 连接级最小权限策略

该策略绑定给**专用普通用户**，不绑定给其他业务用户。替换 Bucket 和前缀后使用：
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

与 AWS／阿里云的“用户仅能扮演某个角色”不同，RustFS 的长期签发用户自身具有上述受限 S3 权限，再通过会话策略收窄。其长期 secret 必须仅保存在控制端，不得下发给 Agent。不要给该用户 `admin:*`、全资源 `readwrite` 或额外管理员组。

RustFS 当前集成以 `s3` 服务名进行 SigV4 签名，在同一个 S3 服务根路径发送 AssumeRole；这与直接使用 AWS STS SDK 的 `sts` 签名服务不同。所需的 STS 接口必须由实际部署提供；反向代理不能剥离 POST 表单、签名头或会话 Token。

## 填写 Add Object Storage

选择 **RustFS**：

| 页面字段 | 内容与获取位置 |
| --- | --- |
| Endpoint URL | 实际 S3 API 主机与端口，例如 `rustfs.example.com:9000`；输入主机端口后通过 TLS 开关决定协议 |
| Region | 实际签名地域；当前表单默认 `us-east-1`，留空会使用该值，有自定义服务端地域时必须一致 |
| Access Key / Secret Key | 专用普通用户 `hfl-sts-controller` 的 access key 和 secret，不是管理员／root 或 Service Account |
| S3 URL Style | Path Style，当前托管集成固定使用该方式 |
| Use TLS (HTTPS) | 生产开启，配置可信证书；内网 HTTP 只在上述受支持条件下使用 |
| Identity Service Endpoint / Role ARN | 本类型不展示这两个输入框；系统用相同服务地址签发，无需配置 AWS IAM 角色 |

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
| 只读 S3 正常，临时凭据签发失败 | 是否误用 root、Service Account、派生 Access Key 或临时身份；普通用户是否绑定有效策略；实际版本是否支持本签发方式 |
| 返回 HTML、登录页或 404 | Endpoint 是否误填为管理 Console、代理是否路由到错误服务或添加了路径前缀 |
| HTTP 地址被拒绝 | 是否使用不被允许的域名／地址；优先部署可信 HTTPS，不关闭后端验证 |
| AccessDenied 或签名失败 | Bucket、前缀、签名地域、服务器时间；代理是否保留完整签名头和 Token |
| 清理不完整 | 版本／删除标记／分片 API 是否实现并授权；检查保留与对象锁限制，不直接授予管理员权限 |

## 配置核对清单

完成存储端配置后，核对以下项目，再在 **Add Object Storage** 中填写连接信息。

- 实际部署已验证普通用户签名的 AssumeRole、内联会话策略与会话 Token 校验。
- 专用普通用户仅绑定本页前缀策略，不使用 root、Service Account、派生 Access Key 或全资源 readwrite。
- S3 与 STS 使用相同协议、主机和端口；代理完整保留签名及 Token，生产环境使用可信 HTTPS。
- Bucket 信息和未完成分片上传列表权限为指定 Bucket 级别；对象内容访问和对象／版本列举仍受仓库前缀限制。

### 所需配置信息

| 配置项 | 核对要求 |
| --- | --- |
| 服务端版本与部署方式 | 实际 RustFS 版本、部署方式及已验证的 STS 能力 |
| Endpoint URL / Use TLS (HTTPS) | 所有执行组件可达的 S3 API 主机、端口和协议，不是管理 Console 地址 |
| Region | 实际签名地域；默认 `us-east-1`，须与服务端配置一致 |
| Bucket | 已创建的专用私有 Bucket 名称 |
| Access Key / Secret Key | 专用普通用户自身的 access key 和 secret，通过安全渠道配置 |
| TLS 与网络 | 证书信任链、DNS、防火墙及可达性要求 |
| Object Prefix | 默认建议 `personal/`；须与本页策略中的仓库前缀一致 |

密钥仅通过安全渠道配置到控制端，不写入普通工单、文档或代码仓库。无需手动创建临时 Token，也无需提供云平台主账号密码。 本类型无需 AWS Role ARN；管理员 alias 和管理员密钥仅用于存储端管理，不用于托管连接。

## 官方参考

- [RustFS IAM：用户、组和策略](https://docs.rustfs.com/en/security-compliance/iam/policies)
- [RustFS Service Account 与 STS](https://docs.rustfs.com/en/security-compliance/iam/sts)
- [RustFS Access Keys](https://docs.rustfs.com/en/security-compliance/iam/access-token)
- [RustFS TLS](https://docs.rustfs.com/en/integration/tls-configured)
