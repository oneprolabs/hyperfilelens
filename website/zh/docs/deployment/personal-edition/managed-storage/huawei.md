---
title: 华为云 OBS 接入
description: 准备普通账号委托、专用 IAM 调用用户和 OBS 最小权限，获取 Agency Name、Domain ID 与托管连接字段。
---

# 华为云 OBS 接入

::: info 适用范围
本指南面向 Personal Edition 运营方，需要企业版支持，并由个人版组织中具有 Managed Storage 管理权限的人员操作。个人版普通用户无需配置云存储身份或密钥。
:::

<p class="hfl-doc-lead">HyperFileLens 通过华为 IAM v3 的普通账号委托签发临时 AK/SK/Token。配置连接前，须区分资源账号、被委托账号和 IAM 用户；本接入不使用云服务委托，账号 ID 也不能以项目 ID 代替。</p>

## 开始前准备

- 准备已存在的私有 OBS Bucket，示例 `hfl-backups-prod`，连接前缀 `personal/`。
- **资源账号**是持有 Bucket、创建委托的账号；**被委托账号**是专用调用 IAM 用户所在的账号。跨账号时两者不同。
- 由有 IAM 管理权限的云管理员确认可用委托关系；同账号部署也要按实际 IAM 能力和管理要求确认，不能把普通账号委托配置为云服务委托。
- 控制端需要访问 IAM Endpoint，执行组件需要访问 Bucket 的 OBS Endpoint；长期密钥不需要拥有 IAM 用户／委托管理权限。

## 创建专用的空 Bucket

1. 使用资源账号打开 **OBS → 桶列表 → 创建桶**，选择所需区域并填写唯一的桶名，记录实际桶名、地域与 OBS Endpoint。
2. 选择**标准存储**与**私有**访问，保持公共访问关闭。基础方案不启用 WORM、归档转换、自动删除对象或自定义 KMS 加密；如需这些功能，须单独验证。
3. 创建后确认桶为空。创建桶由存储管理员执行，不需要给签名用户或委托添加建桶权限。

`hfl-backups-prod` 只是示例，不是预留桶名。策略与表单必须统一替换为实际桶名。`personal/` 是对象键前缀，不需要预先创建目录。已有专用桶仅在确认归属、私有访问、无冲突数据或保留规则后复用。

## 创建专用调用用户

1. 在被委托账号的 **IAM → 用户** 创建 `hfl-sts-controller`，仅用于程序访问，不提供普通控制台登录权限。
2. 由管理员在该用户的凭证管理中创建 AK/SK，或按[访问密钥指南](https://support.huaweicloud.com/intl/zh-cn/perms-cfg-obs/obs_40_0007.html)在受控流程中获取该用户的密钥。不得使用资源账号的主账号密钥。
3. 记录该用户所属的**华为云账号名**，用于下一步委托信任。它不是 IAM 用户名，也不是 HyperFileLens 登录邮箱。

## 创建普通账号委托

1. 使用资源账号的管理员打开 **IAM → 委托 → 创建委托**，名称设为 `hfl-managed-storage`。
2. **委托类型选择「普通账号」**，不是「云服务」。在「委托的账号」中填写专用调用用户所属的华为云**账号名**。
3. 确定委托关系有效期。此处是委托关系的有效期，不是每次临时 AK/SK 的时长。
4. 创建后给委托授权：使用下面的 OBS 自定义策略，并按 OBS 的全局服务授权范围操作。不要添加 `OBS Administrator` 或 `Security Administrator`。

按[创建委托并授权](https://support.huaweicloud.com/usermanual-iam/iam_06_0002.html)核对账号关系；若控制台不接受所选账号关系，应由管理员确认受支持的账号安排，应使用受支持的普通账号委托配置，不应改用云服务委托。

### 账号关系示例

**账号 A** 持有桶并创建委托 `hfl-managed-storage`；**账号 B** 创建 IAM 用户 `hfl-sts-controller` 并保存其 AK/SK。在 A 的委托中，「委托的账号」填写 **B 的华为云账号名**。表单的 Domain ID 填写 **A 的账号 ID**，Agency Name 填写 A 的委托名，AK/SK 使用 B 的专用 IAM 用户密钥。B 的专用用户组只获得调用该名称委托的权限。这里采用官方跨账号委托流程，不代表任意同账号委托配置都可用。

下面的 OBS JSON 是绑定给委托的 **IAM 自定义策略**，应在 IAM 策略编辑器中配置，不是 OBS 桶策略；二者格式不能混用。

## 给委托授予 OBS 前缀权限

在 **IAM → 权限／策略 → 创建自定义策略 → JSON 视图** 创建 `hfl-managed-storage-obs`，替换所有 Bucket 与前缀，然后绑定给上一步委托：
```json
{
  "Version": "1.1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "obs:bucket:ListBucket",
        "obs:bucket:ListBucketVersions"
      ],
      "Resource": [
        "OBS:*:*:bucket:hfl-backups-prod"
      ],
      "Condition": {
        "StringStartWith": {
          "obs:prefix": [
            "personal/repositories/"
          ]
        }
      }
    },
    {
      "Effect": "Allow",
      "Action": [
        "obs:bucket:GetBucketLocation"
      ],
      "Resource": [
        "OBS:*:*:bucket:hfl-backups-prod"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "obs:object:AbortMultipartUpload",
        "obs:object:DeleteObject",
        "obs:object:DeleteObjectVersion",
        "obs:object:GetObject",
        "obs:object:ListMultipartUploadParts",
        "obs:object:PutObject"
      ],
      "Resource": [
        "OBS:*:*:object:hfl-backups-prod/personal/repositories/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "obs:bucket:ListBucketMultipartUploads"
      ],
      "Resource": [
        "OBS:*:*:bucket:hfl-backups-prod"
      ]
    }
  ]
}
```
该策略定义连接级权限上限，覆盖备份、恢复、维护和物理清理。系统签发临时凭据时，进一步按单个仓库和操作类型收窄权限。

- **写入与清理**：`DeleteObject` 用于临时对象清理和仓库维护，因此托管仓库不属于只追加写入存储。版本删除和终止分片上传用于最终清理。
- **Bucket 级元数据**：`obs:bucket:ListBucketMultipartUploads` 是当前控制端清理会话使用的指定 Bucket 元数据权限，不带普通对象列举的前缀条件；桶信息查询也属于桶级权限。因此建议使用专用 Bucket；权限范围同时包含前缀级对象权限与上述 Bucket 级元数据权限。
- **对象访问范围**：对象内容的读取、写入、删除，以及对象／版本列举，均受策略中的前缀限制。连接身份不应获得 Bucket 创建、删除、策略管理或全局管理员权限。

华为 OBS 的条件运算符不能照搬 AWS 的 `StringLike` 通配符语义。这里的连接级父策略使用 `StringStartWith` 限定 `personal/repositories/`；当前产品签发单仓库会话时使用对应仓库精确根路径的 `StringLike`，不在该条件值后追加 AWS 风格的 `*`。对象资源中的 `*` 与列举条件是两回事。参考 [OBS 自定义策略和条件](https://support.huaweicloud.com/usermanual-iam/iam_01_0019.html)。

## 仅允许调用用户使用指定委托

在被委托账号的 IAM 中创建专用用户组，将 `hfl-sts-controller` 加入该组。创建以下**委托调用自定义策略**，按全局服务范围授权给该组，不授予可切换所有委托的宽泛权限：
```json
{
  "Version": "1.1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "iam:tokens:assume"
      ],
      "Resource": {
        "uri": [
          "/iam/agencies/hfl-managed-storage"
        ]
      }
    }
  ]
}
```

上面的 `/iam/agencies/hfl-managed-storage` 使用官方指定委托授权指南中的委托资源形式；委托名称不是该值时替换为实际名称。OBS 数据策略绑定给**委托**，`iam:tokens:assume` 策略授权给**调用用户所在组**，两者不能对调。官方步骤见[将委托权限分配给 IAM 用户](https://support.huaweicloud.com/usermanual-iam/iam_01_0063.html)。

调用策略中的 `/iam/agencies/hfl-managed-storage` 最后一段是**委托名称**，不是委托 ID；名称不同须同步修改。

## 获取账号信息并填写 Add Object Storage

选择 **Huawei Cloud** 和 Bucket 的实际地域。

| 页面字段 | 内容与获取位置 |
| --- | --- |
| Endpoint URL | OBS Bucket 概览中的地域 Endpoint，例如 `obs.cn-north-4.myhuaweicloud.com`；按对应选区目录带出 |
| Region | Bucket 的真实地域，例如 `cn-north-4`，不是 IAM 项目 ID |
| Access Key / Secret Key | 专用调用 IAM 用户的 AK/SK |
| S3 URL Style | 保留对应选区目录默认值，当前华为目录为 Virtual Hosted Style |
| Use TLS (HTTPS) | 开启 |
| Identity Service Endpoint | 当前默认 `https://iam.myhuaweicloud.com`；特殊区域按官方 IAM 接入点确认 |
| Agency Name | **资源账号 → IAM → 委托** 中刚创建的真实委托名称 `hfl-managed-storage`，不是委托 ID |
| Domain ID | **资源账号 → 我的凭证 → API 凭证 → 账号 ID**，32 位十六进制；不是项目 ID、IAM 用户 ID，也不是跨账号调用用户所属账号的 ID |

::: warning IAM Endpoint 的适用范围
`https://iam.myhuaweicloud.eu` 对应欧洲都柏林等官方明确列出的接入范围，不是所有欧洲／国际区域的统一 IAM 地址。OBS Endpoint、IAM Endpoint 与账号体系分别确认。先按默认及实际账号环境验证，特殊区域查看[官方 IAM 接入点](https://support.huaweicloud.com/intl/en-us/api-iam/iam_01_0001.html)，不要因为云平台页面是国际站就直接改成 `.eu`。
:::

账号字段来源见[获取账号、IAM 用户与委托信息](https://support.huaweicloud.com/api-iam/iam_17_0002.html)。

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
| 委托创建时账号找不到 | 填的是被委托方的账号名，不是 IAM 用户名、账号 ID、邮箱或云服务名；由管理员确认账号关系 |
| 临时凭据签发失败 | 委托类型、可信账号、调用用户组的指定委托授权、委托名称及资源账号 Domain ID |
| OBS AccessDenied | OBS 策略是否绑定委托，Bucket 和前缀是否匹配，列举条件是否误用了 AWS 通配符写法 |
| IAM／OBS Endpoint 错误 | 分别核对账号体系、地域和 DNS／TLS，不要把 OBS 地址填入 IAM 字段 |
| 删除清理失败 | 检查历史版本、删除标记、Multipart 授权和保留策略，不用 Bucket 删除权限替代 |

## 配置核对清单

完成存储端配置后，核对以下项目，再在 **Add Object Storage** 中填写连接信息。

- 委托类型为普通账号，可信账号与专用调用 IAM 用户所属账号一致。
- OBS 数据策略绑定给委托；指定委托调用策略通过专用用户组授权给调用用户。
- 调用用户无全局管理员或所有委托访问权限，委托的对象权限限定在约定 Bucket 和仓库前缀内。
- Bucket 信息和未完成分片上传列表权限为指定 Bucket 级别；对象内容访问和对象／版本列举仍受仓库前缀限制。

### 所需配置信息

| 配置项 | 核对要求 |
| --- | --- |
| Bucket | 已创建的专用私有 Bucket 名称 |
| Region / OBS Endpoint | Bucket 的实际地域及对应 OBS Endpoint |
| Identity Service Endpoint | 适用于资源账号的 IAM Endpoint |
| Agency Name | 资源账号中创建的普通账号委托名称 |
| Domain ID | 资源账号的 32 位账号 ID，不是项目 ID 或调用用户 ID |
| 账号关系 | 资源账号与被委托账号的对应关系，不含登录凭据 |
| Access Key / Secret Key | 专用调用 IAM 用户的 AK/SK，通过安全渠道配置 |
| Object Prefix | 默认建议 `personal/`；须与本页策略中的仓库前缀一致 |

密钥仅通过安全渠道配置到控制端，不写入普通工单、文档或代码仓库。无需手动创建临时 Token，也无需提供云平台主账号密码。

## 官方参考

- [创建普通账号委托](https://support.huaweicloud.com/usermanual-iam/iam_06_0002.html)
- [指定委托的细粒度调用授权](https://support.huaweicloud.com/usermanual-iam/iam_01_0063.html)
- [IAM API 授权项](https://support.huaweicloud.com/intl/zh-cn/api-iam/iam_19_0001.html)
- [IAM 自定义策略语法与条件运算符](https://support.huaweicloud.com/usermanual-iam/iam_01_0019.html)
- [OBS IAM 权限与资源范围](https://support.huaweicloud.com/perms-cfg-obs/obs_40_0053.html)
- [账号、项目和委托信息](https://support.huaweicloud.com/api-iam/iam_17_0002.html)
- [IAM 接入点](https://support.huaweicloud.com/intl/en-us/api-iam/iam_01_0001.html)
