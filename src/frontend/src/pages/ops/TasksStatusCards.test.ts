import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/ops/Tasks.vue'), 'utf8')
const locale = readFileSync(resolve(process.cwd(), 'src/locales/en.ts'), 'utf8')

describe('Tasks status cards', () => {
  it('renders every task status represented by task statistics', () => {
    expect(page).toContain('hfl-ops-stats-grid--5')
    expect(page).toContain("t('ops.task.status.running')")
    expect(page).toContain("t('ops.task.status.pending')")
    expect(page).toContain("t('ops.task.status.success')")
    expect(page).toContain("t('ops.task.status.failedTimedOut')")
    expect(page).toContain("t('ops.task.status.cancelled')")
    expect(page).toContain(':value="stats.failed + stats.timeout"')
    expect(page).toContain(':value="stats.cancelled"')
  })

  it('labels the grouped failed and timed out card as Failed', () => {
    expect(locale).toContain("failedTimedOut: 'Failed'")
  })
})
