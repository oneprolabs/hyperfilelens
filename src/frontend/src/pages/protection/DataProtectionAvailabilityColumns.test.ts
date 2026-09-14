import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compactSourceText } from '../../test/sourceText'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const compactPage = compactSourceText(page)
const createWizard = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8')
const restoreTargetCatalog = readFileSync(resolve(process.cwd(), 'src/composables/useRestoreTargetCatalog.ts'), 'utf8')

function tableForStep(step: number) {
  const startMarker = `<div v-if="flowMainStep === ${step}"`
  const start = compactPage.indexOf(startMarker)
  const end = compactPage.indexOf('</el-table>', start)
  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return compactPage.slice(start, end)
}

function expectOrdered(text: string, markers: string[]) {
  let cursor = -1
  for (const marker of markers) {
    const next = text.indexOf(marker, cursor + 1)
    expect(next, `expected ${marker} after ${markers[Math.max(0, markers.indexOf(marker) - 1)]}`).toBeGreaterThan(cursor)
    cursor = next
  }
}

describe('Backup Wizard connectivity columns', () => {
  it.each([0, 1])('places Connectivity after Endpoint and before Lifecycle Status in step %i', (step) => {
    expectOrdered(tableForStep(step), [
      'colConnectionAddress',
      'colConnectivity',
      'colLifecycleStatus',
      'colCpu',
      'colMemory',
      'colDiskCount',
      'colCapacity',
      'colRegisteredAt',
    ])
  })

  it('funds the first two wider Status columns from CPU, Memory, and Disks', () => {
    const connectionWidth = Number(page.match(/connection:\s*(\d+),/)?.[1])
    const statusWidths = [0, 1].map(step =>
      Number(tableForStep(step).match(/colLifecycleStatus'[\s\S]*?width="(\d+)"/)?.[1]),
    )
    const pickWidths = page.match(/const FLOW_PICK_TABLE_COL_MIN = \{[\s\S]*?\} as const/)?.[0] ?? ''
    const cpuWidth = Number(pickWidths.match(/cpu:\s*(\d+),/)?.[1])
    const memoryWidth = Number(pickWidths.match(/memory:\s*(\d+),/)?.[1])
    const diskWidth = Number(pickWidths.match(/diskCount:\s*(\d+),/)?.[1])

    expect(connectionWidth).toBe(118)
    expect(statusWidths).toEqual([168, 168])
    expect([cpuWidth, memoryWidth, diskWidth]).toEqual([79, 90, 87])
    expect(cpuWidth + memoryWidth + diskWidth + statusWidths[0]).toBe(424)
  })

  it('groups source status immediately after task status in step 3', () => {
    const step3Table = tableForStep(2)

    expectOrdered(step3Table, [
      'colConnectionAddress',
      'flowBackupColBackupDirs',
      'flowBackupColTargetRepo',
      'flowBackupColCurrentTaskStatus',
      'flowBackupColRestoreTaskStatus',
      'colConnectivity',
      'colLifecycleStatus',
    ])
    expect(Number(step3Table.match(/colLifecycleStatus'[\s\S]*?width="(\d+)"/)?.[1])).toBe(168)
    expect(page).toContain('const FLOW_START_BACKUP_TABLE_COL_MIN = {\n  connection: 220,\n  backupDirs: 260,\n  compression: 190,\n  targetRepo: 280,\n  binding: 210,\n}')
  })

  it('maps the API availability field into each wizard row', () => {
    expect(page).toContain("availability: item.availability === 'online' ? 'online' : 'offline'")
    expect(page).toContain('flowSourceAvailabilityLabel(row.availability)')
  })

  it('delegates recovery targets to the shared online catalog', () => {
    const start = page.indexOf('async function loadRecoveryTargetHostOptions')
    const end = page.indexOf('function searchRecoveryTargetHostOptions', start)
    const loader = page.slice(start, end)

    expect(start).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(start)
    expect(loader).toContain('restoreTargetCatalog.reset()')
    expect(loader).toContain('restoreTargetCatalog.loadMore()')
    expect(loader).not.toContain("status: 'online'")
    expect(restoreTargetCatalog).toContain("availability: 'online'")
    expect(restoreTargetCatalog).toContain('page_size: RESTORE_TARGET_PAGE_SIZE')
    expect(page).toContain(':remote-method="searchRecoveryTargetHostOptions"')
    expect(page).toContain('@popup-scroll="onRecoveryTargetNodePopupScroll"')
    expect(page).toContain('restoreTargetCatalog.ensureByIds(plans.map((plan) => plan.destHostId).filter(Boolean))')
    expect(page).not.toContain('recoveryTargetHostRequestSeq')
    expect(page).not.toContain('recoveryTargetHostSearchTimer')
  })

  it('uses availability rather than lifecycle status for Backup Setup connectivity checks', () => {
    expect(createWizard).toContain("availability: item.availability === 'online' ? 'online' : 'offline'")
    expect(createWizard).toContain(".filter((row): row is NonNullable<ReturnType<typeof sourceRecord>> => row != null && row.availability !== 'online')")
    expect(createWizard).toContain("const availability = sourceRecord(sourceId)?.availability")
    expect(createWizard).toContain('const restoreTargetCatalog = useRestoreTargetCatalog()')
    expect(createWizard).toContain('restoreTargetCatalog.reset()')
    expect(createWizard).toContain(':remote-method="restoreTargetCatalog.setSearch"')
    expect(createWizard).toContain('@popup-scroll="onCreateRecoveryTargetPopupScroll"')
    expect(createWizard).not.toContain('loadOnlineRecoveryTargets')
    expect(createWizard).not.toContain("item.status === 'online' || item.status === 'reconnecting' ? item.status : 'offline'")
  })

  it('labels the Create Backup Configuration connectivity column consistently', () => {
    expect(createWizard).toContain("protection.sourceResources.colConnectivity")
    expect(createWizard).not.toContain("labelSourceAvailability")
    expect(createWizard).toContain('sourceAvailabilityLabel(row.id)')
    expect(createWizard).toContain('sourceAvailabilityTagType(row.id)')
    expect(createWizard).not.toContain('sourceStatusLabel(row.id)')
  })
})
