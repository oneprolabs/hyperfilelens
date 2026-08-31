---
title: 创建并运行备份
description: 使用备份向导选择目录、目标存储、策略并执行备份。
---

# 创建并运行备份

打开 **Protection → Backup Wizard**。完整流程依次经过 **Backup Sources**、**Backup Configuration**、**Target Storage** 和 **Start Backup**。首次验证建议使用少量合成文件，并手动运行一次备份。

## 1. 选择备份范围

在 **Backup Sources** 选择在线源端并进入 **Backup Configuration**。再次选择源端，打开 **Backup Setup**。

![在 Backup Configuration 中选择已注册的 Windows 源端，主机名、IP 和账户已经模糊处理，注册时间保持可见](/docs/getting-started/select-source-for-setup.png)

在 **Sources** 中展开目录树，选择文件或目录。目录树无法加载时不要直接猜测路径，应先恢复节点连接和目录权限。

选择范围后核对预计数据量。预计值用于发现明显选错路径，不代表最终传输量或存储占用。

![Windows 测试目录已经加入 Selected Paths，主机名和 IP 地址已经模糊处理](/docs/getting-started/select-backup-directory.png)

## 2. 选择目标存储

在 **Target Repository** 为每个备份源选择兼容仓库。向导会根据源端平台、Proxy 绑定和网络能力排除不兼容目标。

保存配置后，系统会验证节点、挂载、写权限、仓库状态和仓库归属。出现验证失败时，先处理界面列出的具体问题，再重试；不要为同一物理位置重复创建新仓库。

![华为云 OBS 仓库已经分配给 Windows 备份源，账户、主机、IP 和仓库信息已经模糊处理](/docs/getting-started/assigned-target-repository.png)

## 3. 配置策略

选择 **Backup Policy**、**File Filter** 和压缩行为。首次验证可以不分配策略和过滤器，先手动运行；确认链路后再启用调度与排除规则。

文件过滤规则会改变进入快照的内容。启用排除扩展名、大小限制、跨文件系统限制或跳过不可读文件后，应在快照详情中确认结果符合预期。

![Backup Policy 和 File Filter 保持可选状态，Windows 主机名和账户已经模糊处理](/docs/getting-started/optional-backup-policy.png)

## 4. 可选恢复计划

恢复计划可以预设：

- 使用最新快照的全部内容或指定范围。
- 恢复目标节点和目录。
- 遇到重名文件时跳过或覆盖。

如果尚未确定恢复位置，可以暂时不启用，产生快照后使用手动恢复向导。第一次恢复验证应使用独立目录和 **Skip**，不要预设覆盖源文件的计划。

## 5. 执行备份

在 **Review** 核对源端、备份路径、目标仓库、压缩设置、策略、过滤器和恢复计划，然后选择 **Create**。

![Review 页面中的备份配置摘要，账户、主机和仓库名称已经模糊处理，恢复路径和配置标签保持可见](/docs/getting-started/review-backup-configuration.png)

返回 **Start Backup** 后确认仓库 **Connectivity** 为 **Online**，选择源端并执行 **Backup Now**。在 **Backup Task** 列等待状态变为 **Succeeded**。

![备份配置已准备运行，Connectivity 为 Online，账户和主机信息已经模糊处理](/docs/getting-started/backup-ready-to-run.png)

运行期间不要重启 Agent、卸载 NAS 或修改目标凭据。任务结束后继续[查看任务与快照](/zh/docs/backup-restore/snapshots)。
