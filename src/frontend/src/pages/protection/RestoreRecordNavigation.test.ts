import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const drawer = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue'),
  'utf8',
)

function sourceBetween(source: string, start: string, end: string) {
  const startIndex = source.indexOf(start)
  const endIndex = source.indexOf(end, startIndex)
  expect(startIndex).toBeGreaterThanOrEqual(0)
  expect(endIndex).toBeGreaterThan(startIndex)
  return source.slice(startIndex, endIndex)
}

describe('restore record contextual navigation', () => {
  it('opens the latest restore record instead of the generic task drawer', () => {
    const handler = sourceBetween(page, 'function openLatestRestoreTask', 'function openRestoreTaskStatusDrawer')

    expect(handler).toContain('const record = latestRestoreRecordForSource(row.id)')
    expect(handler).toContain("tab: 'restoreRecords'")
    expect(handler).toContain('restoreRecordId: record.id')
    expect(handler).toContain('restoreRecordTaskUuid: record.task_uuid')
    expect(handler).not.toContain('openTaskDetail(')
    expect(page).toContain(':initial-restore-record-id="flowSourceDetailRestoreRecordId"')
    expect(page).toContain(':initial-restore-record-task-uuid="flowSourceDetailRestoreRecordTaskUuid"')
  })

  it('loads a targeted record outside the current page and expands it', () => {
    const resolver = sourceBetween(drawer, 'async function resolveTargetedRestoreRecord', 'async function loadRestoreRecordsForSource')
    const loader = sourceBetween(drawer, 'async function loadRestoreRecordsForSource', 'function stopRestoreRecordPolling')

    expect(resolver).toContain('task_uuid: taskUuid')
    expect(resolver).toContain('record.id === targetId')
    expect(resolver).toContain('restoreRecordMatchesEndpoint(record, endpoint)')
    expect(drawer).toContain('return [target, ...restoreRecords.value]')
    expect(drawer).toContain(':data="displayedRestoreRecords"')
    expect(loader).toContain('expandedRestoreRecordRowKeys.value = [target.id]')
    expect(loader).toContain('void loadRestoreRecordRuntime(target)')
  })

  it('clears targeted records and runtime state across navigation boundaries', () => {
    const sourceWatcher = sourceBetween(drawer, 'watch(sourceId,', 'watch(activeTaskUuid,')
    const closeHandler = sourceBetween(drawer, 'function onClosed()', '</script>')

    for (const source of [sourceWatcher, closeHandler]) {
      expect(source).toContain('targetedRestoreRecord.value = null')
      expect(source).toContain('appliedRestoreRecordTargetId.value = null')
      expect(source).toContain('restoreRecordRuntimeById.value = new Map()')
      expect(source).toContain('restoreRecordRuntimeLoadingIds.value = new Set()')
      expect(source).toContain('resetExpandedRestoreItems()')
    }
  })

  it('shows runtime metrics with an explicit unavailable fallback', () => {
    expect(drawer).toContain('restoreRecordRuntimeMetricParts(')
    expect(drawer).toContain('restoreRecordRuntime(record)')
    expect(drawer).toContain('restoreRecordTaskStatus(record)')
    expect(drawer).toContain("t('protection.backupsPage.flowRestoreRecordMetricsUnavailable')")
    expect(drawer).toContain('@click.stop="openTaskDetailByUuid(row.task_uuid)"')
  })
})
