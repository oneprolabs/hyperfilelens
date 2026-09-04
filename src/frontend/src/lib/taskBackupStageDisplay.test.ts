import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import { compactSourceText } from '../test/sourceText'
import { taskStepTimelineTone, taskStepTranslationKey } from './taskStepDisplay'

const taskDetailSurfaces = [
  'src/pages/ops/Tasks.vue',
  'src/pages/protection/components/TaskDetailDrawer.vue',
  'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue',
] as const

describe('backup task stage display', () => {
  it('uses user-facing names for the backup workflow', () => {
    expect(en.ops.task.step.create_logic_snapshot).toBe('Preparing Backup')
    expect(en.ops.task.step.kopia_snapshot).toBe('Syncing Data')
    expect(en.ops.task.step.finalize_snapshot).toBe('Finalizing Backup')
  })

  it('uses the shared translation key resolver on every task-detail surface', () => {
    for (const path of taskDetailSurfaces) {
      const source = compactSourceText(readFileSync(resolve(process.cwd(), path), 'utf8'))
      expect(source).toContain("import { taskStepTimelineTone, taskStepTranslationKey } from")
      expect(source).toContain('taskStepTranslationKey(stepName, taskType')
      expect(source).toContain('taskStepTimelineTone(status)')
      expect(source).toContain('timelineIconClass(step.status)')
      expect(source).toContain('<TaskStatusTag :status="step.status" />')
    }
  })

  it('preserves special snapshot-download labels without changing backend step names', () => {
    expect(taskStepTranslationKey('restore', 'snapshot_download'))
      .toBe('ops.task.step.snapshot_download_restore')
    expect(taskStepTranslationKey('kopia_snapshot', 'backup'))
      .toBe('ops.task.step.kopia_snapshot')
  })

  it.each([
    ['success', 'success'],
    ['running', 'running'],
    ['pending', 'pending'],
    ['failed', 'danger'],
    ['cancelled', 'muted'],
    ['skipped', 'muted'],
    ['warning', 'warning'],
  ] as const)('maps %s steps to a distinct %s timeline tone', (status, tone) => {
    expect(taskStepTimelineTone(status)).toBe(tone)
  })
})
