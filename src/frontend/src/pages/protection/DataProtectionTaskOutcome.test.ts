import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')

describe('Data Protection task outcome columns', () => {
  it('uses the shared compact outcome for both Backup Task and Restore Task', () => {
    expect(page).toContain("import TaskTerminalOutcomeCell from './components/TaskTerminalOutcomeCell.vue'")
    expect(page.match(/<TaskTerminalOutcomeCell/g)).toHaveLength(2)
    expect(page).toContain(':task="latestBackupTaskForSource(row.id)"')
    expect(page).toContain(':fallback="latestSnapshotForSource(row.id)"')
    expect(page).toContain(':task="latestRestoreTaskForSource(row.id)"')
    expect(page).toContain(':fallback="latestRestoreRecordForSource(row.id)?.task_summary"')
  })

  it('preserves the running and stopping progress presentations', () => {
    expect(page.match(/<TaskProgressCell/g)?.length).toBeGreaterThanOrEqual(4)
    expect(page).toContain("sourceBackupCellPhase(row.id) === 'running'")
    expect(page).toContain("sourceBackupCellPhase(row.id) === 'stopping'")
    expect(page).toContain("sourceRestoreCellPhase(row.id) === 'running'")
    expect(page).toContain("sourceRestoreCellPhase(row.id) === 'stopping'")
  })

  it('keeps Insight workspace restores out of Protection task state', () => {
    expect(page).toContain("exclude_insight_workspace_restores: 'true'")
  })
})
