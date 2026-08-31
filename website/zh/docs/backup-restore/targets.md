---
title: 管理目标存储
description: 配置并验证对象存储、NAS 和 Proxy 本地磁盘。
---

# 管理目标存储

目标存储用于保存备份仓库。选择存储时，需要同时考虑源端可达性、容量、保留周期和恢复读取速度。

在英文界面打开 **Protection → Backup Wizard → Target Storage**，选择 **Add Repository**。仓库创建后还需要分配给备份源；仅出现在仓库列表中并不代表备份配置已经使用它。

## 对象存储

支持 AWS S3、阿里云 OSS、华为云 OBS 和受支持的 S3 兼容服务。根据页面填写端点、区域、存储桶、对象前缀和访问凭据。

凭据应只具备所需存储范围的最小权限，目标对象前缀应专用于当前 HyperFileLens 仓库。

![Add Repository 中的对象存储服务商选择，账户信息已经模糊处理](/docs/getting-started/select-huawei-cloud.png)

对象存储配置通常包含 **Endpoint**、**Region**、**Bucket**、**Object Prefix**、**Access Key** 和 **Secret Key**。Access Key 和 Secret Key 属于秘密信息：不要在截图中使用模糊处理，应让字段保持为空、使用合成值，或对完整值进行不透明覆盖。

![华为云 OBS 仓库配置表单，账户、Bucket、Object Prefix 和访问凭据已经遮盖，公开 Endpoint、Region 和 SSL 设置保持可见](/docs/getting-started/configure-huawei-repository.png)

## NAS

NAS 目标可以绑定 Proxy，由 Proxy 挂载共享并提供仓库访问。源端、Proxy 与 NAS 之间必须具备实际可用的网络路径和协议权限。

## Proxy 本地磁盘

选择 Proxy 主机上的专用绝对路径。不要使用系统临时目录、其他应用目录或已有业务数据目录，并为快照增长和恢复读取预留空间。

## 保存前检查

- 连接和写入验证通过。
- 目标位置未被其他仓库占用。
- 凭据、TLS、DNS 和系统时间正确。
- 容量能够覆盖预期数据增长和保留周期。

保存后返回 **Target Storage**，确认仓库 **Connectivity** 为 **Online**，再在 **Backup Configuration** 中把它分配给对应源端。

![华为云 OBS 仓库创建完成且 Connectivity 为 Online，账户、仓库名、Bucket 和 Object Prefix 已经模糊处理](/docs/getting-started/huawei-repository-created.png)

连接失败时优先检查 Endpoint、Region、凭据权限、TLS、DNS、系统时间以及源端或 Proxy 到存储服务的网络路径。不要为同一个物理 Bucket 和 Object Prefix 反复创建仓库。
