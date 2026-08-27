---
title: 业务流程
description: 了解 HyperFileLens 备份、快照、恢复和智能洞察之间的业务关系。
---

# 业务流程

HyperFileLens 以备份快照为核心。备份配置将需要保护的数据、目标存储和执行策略关联起来；备份任务生成快照后，可以恢复文件和目录，也可以从快照中选择数据开展智能洞察。

## 整体流程

**备份源与目标存储 → 备份配置 → 备份任务 → 快照 → 恢复或智能洞察**

快照是备份结果，也是恢复和智能洞察共同使用的数据基础。建议根据业务要求定期开展恢复演练，确认恢复范围和流程满足预期。

## 备份与恢复

备份与恢复覆盖完整的数据保护周期：

1. 添加[备份源](/zh/docs/backup-restore/sources)和[目标存储](/zh/docs/backup-restore/targets)，并验证数据访问。
2. [创建并运行备份](/zh/docs/backup-restore/create-backup)，按需设置[策略与保留](/zh/docs/backup-restore/policies)。
3. [查看任务与快照](/zh/docs/backup-restore/snapshots)，确认预期文件已经进入快照。
4. [恢复文件和目录](/zh/docs/backup-restore/restore)，验证备份数据可以实际使用。

需要完整说明时，请进入[备份与恢复使用流程](/zh/docs/backup-restore/)。

## 智能洞察

智能洞察使用已经生成的备份快照，不直接读取备份主机上的实时文件：

1. 选择备份配置、具体快照以及需要分析的文件或目录。
2. 使用默认的公共 Data Gateway，或选择能够访问备份仓库的 Private Data Gateway。
3. 创建洞察会话，等待所选数据准备完成后开始提问。
4. 通过引用了解回答依据和信息来源。

需要完整说明时，请进入[智能洞察使用流程](/zh/docs/insights/)。

## 首次使用

第一次使用 HyperFileLens 时，建议按照[快速开始](/zh/docs/)使用同一份测试数据依次完成备份、恢复和智能洞察。完成首次流程后，再根据实际数据量和业务要求配置策略、保留规则及日常验证计划。
