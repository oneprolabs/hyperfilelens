import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../../../locales/en'
import { enProtectionPages } from '../../../locales/enProtectionPages'
import { compactSourceText } from '../../../test/sourceText'

const drawer = compactSourceText(
  readFileSync(resolve(process.cwd(), 'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue'), 'utf8'),
)

function sourceBetween(start: string, end: string) {
  const startIndex = drawer.indexOf(start)
  const endIndex = drawer.indexOf(end, startIndex)
  expect(startIndex).toBeGreaterThanOrEqual(0)
  expect(endIndex).toBeGreaterThan(startIndex)
  return drawer.slice(startIndex, endIndex)
}

function buttonWithHandler(source: string, handler: string, marker: string) {
  let handlerIndex = source.indexOf(handler)
  while (handlerIndex >= 0) {
    const startIndex = source.lastIndexOf('<button', handlerIndex)
    const endIndex = source.indexOf('</button>', handlerIndex)
    expect(startIndex).toBeGreaterThanOrEqual(0)
    expect(endIndex).toBeGreaterThan(handlerIndex)
    const button = source.slice(startIndex, endIndex)
    if (button.includes(marker)) return button
    handlerIndex = source.indexOf(handler, handlerIndex + handler.length)
  }
  throw new Error(`Unable to find button containing ${handler} and ${marker}`)
}

describe('FlowBackupSourceDetailDrawer task columns', () => {
  it('removes Current Step and keeps the remaining columns within the full-size drawer', () => {
    const tasksTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupDetail.tabTasks\')" name="tasks">',
      '<ElDrawer v-model="taskAdvancedFilterOpen"',
    )

    expect(tasksTab).not.toContain("t('protection.backupsPage.flowTaskColPhase')")
    expect(tasksTab).toContain('<el-table-column :label="t(\'ops.task.colName\')" width="275" fixed>')
    expect(tasksTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colTaskType\')" width="205">')
    expect(tasksTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colTaskStatus\')" width="115">')
    expect(tasksTab).toContain('<el-table-column :label="t(\'protection.backupsPage.flowTaskColProgress\')" min-width="165">')
    expect(tasksTab).toContain('<el-table-column :label="t(\'ops.task.colTrigger\')" width="105">')
    expect(tasksTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colCreated\')" min-width="160">')

    expect(275 + 205 + 115 + 165 + 105 + 160).toBeLessThanOrEqual(1040)
  })

  it('keeps insight workspace restores out of the Protection task list', () => {
    const loader = sourceBetween(
      'async function loadTasksForSource()',
      'function refreshSourceDetailData()',
    )

    expect(loader).toContain("exclude_insight_workspace_restores: 'true'")
  })
})

describe('FlowBackupSourceDetailDrawer source status', () => {
  it('uses explicit connectivity and lifecycle status terminology', () => {
    const sourceInfo = sourceBetween(
      "<h4 class=\"hfl-detail-section__title\">{{ t('protection.backupsPage.flowSourceDetailSectionMeta') }}</h4>",
      '<section class="hfl-detail-section dp-flow-config-section">',
    )

    expect(drawer).toContain('availability: item.availability')
    expect(drawer).toContain('status: item.status')
    expect(sourceInfo).toContain("flowSourceDetailSourceStatus")
    expect(sourceInfo).toContain("flowSourceDetailConnectivity")
    expect(sourceInfo).toContain('flowSourceStatusLabel(overviewSource.availability)')
    expect(sourceInfo).toContain('flowSourceLifecycleStatusLabel(overviewSource.status)')
    expect(drawer).toContain('flowSourceStatusLabel(source.availability)')
    expect(drawer).not.toContain('flowSourceStatusLabel(source.status)')
    expect(sourceInfo).toContain('flowSourceStatusLabel(overviewSource.availability)')
    expect(sourceInfo.indexOf('flowSourceDetailSourceStatus')).toBeLessThan(sourceInfo.indexOf('flowSourceSecondaryInfo(overviewSource).nameLabel'))
    expect(sourceInfo.indexOf('flowSourceSecondaryInfo(overviewSource).ipLabel')).toBeLessThan(sourceInfo.indexOf('flowSourceDetailRegistered'))
    expect(enProtectionPages.backupsPage.flowSourceDetailSourceStatus).toBe('Lifecycle Status')
    expect(enProtectionPages.backupsPage.flowSourceDetailConnectivity).toBe('Connectivity')
  })
})

describe('FlowBackupSourceDetailDrawer snapshot expansion state', () => {
  it('blocks snapshot restore actions while the source backup is active', () => {
    const restoreGuard = sourceBetween(
      'function canRestoreSnapshot(row: BackupSourceSnapshot)',
      'function onSnapshotExpandChange',
    )

    expect(drawer).toContain('restoreBlockedByBackup?: boolean')
    expect(restoreGuard).toContain('!props.restoreBlockedByBackup && isSnapshotRestorable(row)')
    expect(restoreGuard).toContain("t('protection.backupsPage.msgBackupActiveBlocksActions')")
  })

  it('distinguishes viewing a snapshot from browsing a directory', () => {
    const snapshotTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabSnapshots\')" name="snapshots">',
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
    )
    const snapshotAction = buttonWithHandler(
      snapshotTab,
      '@click.stop="toggleSnapshot(row)"',
      'snapshot-point-actions__button',
    )
    const directoryAction = buttonWithHandler(
      snapshotTab,
      '@click.stop="openSnapshotDirectory(dir)"',
      'snapshot-point-actions__button',
    )

    expect(snapshotAction).toContain("t('protection.backupsPage.snapshotViewAction')")
    expect(snapshotAction).not.toContain("t('protection.backupsPage.snapshotBrowserBrowse')")
    expect(directoryAction).toContain("t('protection.backupsPage.snapshotBrowserBrowse')")
    expect(directoryAction).not.toContain("t('protection.backupsPage.snapshotViewAction')")
    expect(enProtectionPages.backupsPage.snapshotViewAction).toBe('View')
    expect(enProtectionPages.backupsPage.snapshotBrowserBrowse).toBe('Browse')
  })

  it('allocates enough width for both snapshot timestamps', () => {
    const snapshotTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabSnapshots\')" name="snapshots">',
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
    )

    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colSnapId\')" width="140" fixed>')
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colSnapStart\')" width="160">')
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colSnapEnd\')" width="160">')
  })

  it('preserves loaded snapshot details when the active tab refreshes its list', () => {
    const loader = sourceBetween(
      'async function loadSnapshotsForSource()',
      'async function downloadSelectedBrowserPaths()',
    )

    expect(loader).not.toContain('snapshotDetails.value = new Map()')
    expect(loader).not.toContain('expandedSnapshotRowKeys.value = []')
    expect(loader).not.toContain('selectedSnapshotId.value = null')
  })

  it('hands ready snapshot artifacts to the browser without buffering a Blob', () => {
    const downloader = sourceBetween(
      'async function startNativeArtifactDownload',
      'function closeSnapshotFileBrowser',
    )

    expect(downloader).toContain('createSnapshotArtifactDownloadUrl(artifactId)')
    expect(downloader).toContain('anchor.href = result.url')
    expect(downloader).not.toContain('URL.createObjectURL')
    expect(downloader).not.toContain('.blob()')
  })

  it('clears cached expansion state when pagination changes', () => {
    const paginationWatcher = sourceBetween(
      '() => [snapshotPagination.page, snapshotPagination.pageSize] as const,',
      'watch(sourceId,',
    )

    expect(paginationWatcher).toContain('selectedSnapshotId.value = null')
    expect(paginationWatcher).toContain('expandedSnapshotRowKeys.value = []')
    expect(paginationWatcher).toContain('snapshotDetails.value = new Map()')
  })
})

describe('FlowBackupSourceDetailDrawer task step expansion feedback', () => {
  it('disables expansion and explains when a task step has no events', () => {
    const toggleStep = sourceBetween(
      'function toggleStep(stepId: number | string, eventCount: number)',
      'function setAllStepsExpanded',
    )
    const taskSteps = sourceBetween(
      '<div v-if="stepsWithEvents.length" class="dp-task-detail__step-list">',
      '<div v-if="unlinkedTaskEvents.length > 0"',
    )

    expect(toggleStep).toContain('if (eventCount === 0)')
    expect(toggleStep).not.toContain('ElMessage.info')
    expect(toggleStep.indexOf('return')).toBeLessThan(toggleStep.indexOf('expandedTaskSteps[key]'))
    expect(taskSteps).toContain(':aria-expanded="step.events.length > 0 && isStepExpanded(step.id)"')
    expect(taskSteps).toContain(':aria-disabled="step.events.length === 0"')
    expect(taskSteps).toContain('@click="toggleStep(step.id, step.events.length)"')
    expect(taskSteps).toContain(':content="t(\'ops.task.emptyEvents\')"')
    expect(taskSteps).toContain('append-to="body"')
    expect(taskSteps).toContain(':z-index="3600"')
    expect(taskSteps).toContain('hfl-task-step-chevron--disabled')
    expect(taskSteps).toContain('v-if="step.events.length > 0 && isStepExpanded(step.id)"')
    expect(en.ops.task.emptyEvents).toBe('No events are available for this step.')
  })
})
