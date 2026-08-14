import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')

describe('restore wizard source selection', () => {
  it('restores the table checkboxes when the wizard closes', () => {
    const start = page.indexOf('watch(recOpen, (open) => {')
    const end = page.indexOf('watch([recOpen, recBackupId]', start)
    const watcher = page.slice(start, end)

    expect(start).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(start)
    expect(watcher).toContain('if (!open && !isFixedSnapshotRestore.value)')
    expect(watcher).toContain('nextTick(() => syncStep3TableSelection())')
  })
})
