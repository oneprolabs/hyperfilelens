---
title: Windows 文件属性与权限限制
description: 了解 HyperFileLens 在 Windows 备份和恢复过程中当前能够保留的文件属性与权限范围。
---

# Windows 文件属性与权限限制

HyperFileLens 可以备份和恢复 Windows 主机上的文件内容，也可以处理
通过 Windows 可访问的 SMB 共享。文件内容恢复和 Windows 文件系统元数据
保留是两种不同的能力。恢复成功只表示选定内容已经写入目标路径，不表示
Windows 文件属性或 NTFS 权限已经被备份或恢复。

## 当前支持边界

当前版本通过产品的文件系统快照和恢复引擎执行备份与恢复。该引擎的元数据
模型会记录文件名、目录结构、文件内容、时间信息和基础 mode 信息，但不
记录 Windows 特有文件属性和 NTFS Security Descriptor。因此，当前版本
无法从快照中恢复这些信息。

| 能力 | 当前行为 |
| --- | --- |
| 文件内容 | 备份和恢复均支持 |
| 文件名和目录结构 | 支持备份和恢复，但受文件系统和路径限制影响 |
| 修改时间 | 恢复引擎会尝试恢复；目标文件系统可能拒绝设置或改变结果 |
| Windows `Hidden` 属性 | 不备份，也不恢复 |
| Windows `System` 属性 | 不备份，也不恢复 |
| Windows `Archive` 属性 | 不备份，也不恢复 |
| Windows `Read-only` 属性 | 不作为 Windows 属性备份或恢复；恢复后的写保护状态可能不同 |
| NTFS Owner 和 Security Descriptor | 不备份，也不恢复 |
| NTFS Allow/Deny ACE | 不备份，也不恢复 |
| Active Directory 用户和组 SID | 不备份，也不恢复 |
| NTFS 继承关系和继承 ACE | 不备份，也不恢复 |

因此，源端设置了 `Hidden` 的文件，恢复时不会从快照获得 `Hidden` 值，
Windows 可能将恢复后的文件显示为可见文件。`System` 和 `Archive` 属性
也遵循相同规则。

## NTFS 权限与 Active Directory ACL

当前版本不备份或恢复文件、目录的 Windows Security Descriptor。以下源端
信息不会进入备份元数据，也不会在恢复时从备份元数据重建：

- Owner；
- 本地用户、域用户和组 SID；
- Allow 和 Deny 访问控制条目；
- 显式权限和继承权限；
- 继承标记以及受保护 ACL 状态；
- `BUILTIN\Administrators` 等组相关权限。

例如，源文件中存在：

```text
BUILTIN\Administrators:(F)
```

当前版本不会将这条源端 ACE 备份到快照，恢复时也不会从快照写入这条
ACE。不能把文件内容恢复成功作为源端 ACL 已经恢复的证明。

恢复目标仍可能因为自身的目录继承或文件系统默认设置而具有某些权限，
但这些目标权限不是源端 ACL，也不表示 ACL 已经恢复。

## 通过 Linux Proxy 连接 SMB 或 CIFS 共享

当 SMB 或 CIFS 共享通过 Linux Proxy 挂载时，备份引擎看到的是 Linux 挂载
暴露出来的文件系统视图。当前版本在此路径下不备份或恢复原始 Windows
Security Descriptor、域用户和组 SID、DACL 以及继承行为。

增加 `mount.cifs` 的 ACL 相关选项，可能改变 Linux 挂载层对权限的呈现方式，
但不会使 HyperFileLens 获得 Windows ACL 的采集或恢复能力。无论共享是
备份源还是恢复目标，都适用此限制。

## 恢复验证建议

如果数据依赖 Windows 权限或文件属性，建议：

1. 先将有代表性的样本恢复到独立测试目录；
2. 检查文件内容、文件名和目录结构；
3. 使用目标系统的原生工具检查 Windows 文件属性和 ACL；
4. 使用实际 Agent 账户和实际目标文件系统验证结果；
5. 如果合规或运维要求依赖精确 ACL 保真，另外保留原生 Windows 或
   AD-aware 权限备份。

不要把 HyperFileLens 的内容恢复结果作为唯一的 ACL 备份。当前版本不会
创建能够恢复源端 Windows Security Descriptor 的 ACL 备份。

## 后续支持

当前没有任何版本承诺支持 Windows 属性或 NTFS ACL 保真。未来版本必须新增
并验证 Windows 文件属性、Security Descriptor、SID 身份、权限要求以及
SMB/NAS 目标行为后，本页和支持范围文档才会声明支持。

本页说明适用于当前产品版本的支持边界。
