import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const taskPresentationFiles = [
  'src/pages/Dashboard.vue',
  'src/pages/ops/Tasks.vue',
  'src/pages/node/Repositories.vue',
  'src/pages/protection/BackupDetail.vue',
  'src/pages/protection/DataProtection.vue',
  'src/pages/protection/components/TaskDetailDrawer.vue',
  'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue',
  'src/pages/protection/components/KopiaTransferProgress.vue',
  'src/pages/protection/components/TaskProgressCell.vue',
  'src/components/ProtectionStopConfirmDialog.vue',
  'src/lib/protectionStopConfirm.ts',
  'src/styles/ops-list-ui.css',
]

const taskProgressPresentationPatterns = [
  /<el-progress\b/i,
  /\bformatTaskProgress(?:Bar)?Percent\b/,
  /\b(?:running-(?:track|fill)|running-item__pct)\b/,
  /\b(?:hfl-task-list|hfl-task-drawer|dp-task-detail|dp-source-task|reset-status-cell)__(?:progress|track|fill|percent)\b/,
  /\b(?:tasksProgress|colProgress|flowTaskColProgress|progressLabel|stopConfirmColProgress)\b/,
  /\s:progress="/,
]

describe('task progress presentation', () => {
  it.each(taskPresentationFiles)('does not render a task bar or percentage in %s', (relativePath) => {
    const source = readFileSync(resolve(process.cwd(), relativePath), 'utf8')

    for (const pattern of taskProgressPresentationPatterns) {
      expect(source).not.toMatch(pattern)
    }
  })

  it('uses Start Time and End Time for task execution timestamps', () => {
    const tasks = readFileSync(resolve(process.cwd(), 'src/pages/ops/Tasks.vue'), 'utf8')
    const repositories = readFileSync(resolve(process.cwd(), 'src/pages/node/Repositories.vue'), 'utf8')
    const backupDetail = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupDetail.vue'), 'utf8')
    const dataProtection = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
    const sharedDrawer = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/TaskDetailDrawer.vue'), 'utf8')
    const sourceDrawer = readFileSync(resolve(process.cwd(), 'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue'), 'utf8')
    const taskTimeSources = [tasks, repositories, backupDetail, dataProtection, sharedDrawer, sourceDrawer].join('\n')

    expect(taskTimeSources).not.toMatch(/ops\.task\.(?:startedAt|finishedAt|timeFieldFinished)/)
    expect(taskTimeSources).not.toContain('repositoriesPage.tasksCreated')
    expect(taskTimeSources).not.toContain("t('protection.backupsPage.flowRestoreRecordFinishedAt')")

    expect(tasks).toContain(":label=\"t('ops.task.startTime')\"")
    expect(tasks).toContain(":label=\"t('ops.task.endTime')\"")
    expect(repositories).toContain("t('repositoriesPage.tasksStartTime')")
    expect(repositories).toContain('formatLocalDateTime(row.started_at || row.created_at)')
    expect(repositories).toContain("t('repositoriesPage.tasksEndTime')")
    expect(repositories).toContain('formatLocalDateTime(row.finished_at)')
    expect(backupDetail).toContain('startTime: task.started_at || task.created_at')
    expect(backupDetail).toContain('endTime: task.finished_at')
    expect(dataProtection).toContain("t('protection.backupDetail.colStart')")
    expect(dataProtection).toContain("t('protection.backupDetail.colEnd')")
    expect(sourceDrawer).toContain('formatNullableTime(row.started_at || row.created_at)')
    expect(sourceDrawer).toContain('formatNullableTime(row.finished_at)')
    expect(taskTimeSources).not.toMatch(/finished_at\s*\|\|\s*(?:row\.|task\.)?created_at/)
  })

  it('leaves non-task dashboard utilization indicators intact', () => {
    const dashboard = readFileSync(resolve(process.cwd(), 'src/pages/Dashboard.vue'), 'utf8')

    expect(dashboard).toContain('ribbon-card__progress-block')
    expect(dashboard).toContain('storageUsedProgressLabel')
  })
})
