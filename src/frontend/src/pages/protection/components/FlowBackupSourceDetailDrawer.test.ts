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

describe('FlowBackupSourceDetailDrawer backup policy overview', () => {
  it('aligns schedule, retention, and advanced policy details with the backup wizard', () => {
    expect(drawer).toContain('function policyScheduleDetailRows')
    expect(drawer).toContain('function policyScheduleCycleValue')
    expect(drawer).toContain("t('protection.policiesPage.scheduleCycle')")
    expect(drawer).toContain("t('protection.policiesPage.scheduleTimezone')")
    expect(drawer).toContain("t('protection.policiesPage.scheduleStartsAt')")
    expect(drawer).toContain("t('protection.policiesPage.shortDesc'")
    expect(drawer).toContain("t('protection.policiesPage.midDesc'")
    expect(drawer).toContain("t('protection.policiesPage.longDesc'")
    expect(drawer).toContain('function policyAdvancedDetailLines')
    expect(drawer).toContain('policyAdvancedDetailLines(currentSourcePolicy)')
    expect(drawer).toContain('class="policy-retention-detail-list__line dp-flow-policy-overview__retention-line"')
    expect(drawer).not.toContain(':class="{ \'policy-retention-detail-list__line--summary\': !line.label }"')
    expect(drawer).toContain('.dp-flow-policy-overview__advanced-box { width: 100%; box-sizing: border-box; }')
  })
})

describe('FlowBackupSourceDetailDrawer target validation refresh', () => {
  it('polls provisioning status only while the overview is open and stops at a terminal result', () => {
    const loader = sourceBetween(
      'async function loadOverviewForSource',
      'async function loadSnapshotsForSource',
    )
    const polling = sourceBetween(
      'function stopProvisionPolling',
      'async function loadTasksForSource',
    )

    expect(loader).toContain('options: { silent?: boolean } = {}')
    expect(loader).toContain('if (!silent)')
    expect(polling).toContain('PROVISION_POLL_MAX_ATTEMPTS')
    expect(polling).toContain('provisionPollingInFlight')
    expect(polling).toContain("activeTab.value !== 'overview'")
    expect(polling).toContain("currentSourceConfig.value?.status !== 'provisioning'")
    expect(polling).toContain("emit('config-changed')")
    expect(polling).toContain('stopProvisionPolling()')
    expect(drawer).toContain('stopProvisionPolling()')
  })
})

describe('FlowBackupSourceDetailDrawer snapshot expansion state', () => {
  it('adds snapshot filters and refresh without an advanced-filter entry', () => {
    const snapshotTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabSnapshots\')" name="snapshots">',
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
    )
    const loader = sourceBetween(
      'async function loadSnapshotsForSource()',
      'async function downloadSelectedBrowserPaths()',
    )

    expect(snapshotTab).toContain('v-model="snapshotFilterId"')
    expect(snapshotTab).toContain('v-model="snapshotFilterStatus"')
    expect(snapshotTab).toContain('v-model="snapshotFilterTimeMode"')
    expect(snapshotTab).toContain('@click="loadSnapshotsForSource"')
    expect(snapshotTab).not.toContain('advancedFilter')
    expect(loader).toContain('snapshot_uid: appliedSnapshotFilterId.value || undefined')
    expect(loader).toContain('status: snapshotFilterStatus.value || undefined')
    expect(loader).toContain('started_from: startedRange.from')
    expect(loader).toContain('started_to: startedRange.to')
  })

  it('allows snapshot restore actions while the source backup is active', () => {
    const restoreGuard = sourceBetween(
      'function canRestoreSnapshot(row: BackupSourceSnapshot)',
      'function onSnapshotExpandChange',
    )

    expect(drawer).not.toContain('restoreBlockedByBackup?: boolean')
    expect(drawer).toContain('restoreBlockedByRestore?: boolean')
    expect(restoreGuard).toContain('!props.restoreBlockedByRestore && isSnapshotRestorable(row)')
    expect(restoreGuard).not.toContain('restoreBlockedByBackup')
  })

  it('shows storage metrics without the reference explanation header', () => {
    const snapshotTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabSnapshots\')" name="snapshots">',
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
    )
    const expandedSummary = sourceBetween(
      '<section class="snapshot-efficiency-summary">',
      '<el-table v-if="selectedSnapshotDirectories.length"',
    )

    expect(snapshotTab).toContain("t('protection.backupsPage.snapshotListSize')")
    expect(snapshotTab).toContain("t('protection.backupsPage.snapshotRecoverableData')")
    expect(snapshotTab).toContain('fmtReferenceBytes(row.new_packed_content_bytes)')
    expect(expandedSummary).not.toContain('snapshot-efficiency-summary__header')
    expect(expandedSummary).not.toContain("t('protection.backupsPage.snapshotStorageEfficiencyTitle')")
    expect(expandedSummary).not.toContain("t('protection.backupsPage.snapshotStorageEfficiencyLead')")
    expect(expandedSummary).not.toContain("t('protection.backupsPage.snapshotStorageReferenceHint')")
    expect(expandedSummary).toContain('selectedSnapshot.new_original_content_bytes')
    expect(expandedSummary).toContain('selectedSnapshot.new_packed_content_bytes')
    expect(expandedSummary).toContain('selectedSnapshot.data_reuse_ratio')
    expect(expandedSummary).toContain('selectedSnapshot.compression_savings_ratio')
    expect(expandedSummary).toContain('fmtCombinedReduction(selectedSnapshot)')
    expect(expandedSummary.match(/snapshot-efficiency-summary__metric-info/g)).toHaveLength(6)
    expect(expandedSummary.match(/append-to="body"/g)).toHaveLength(6)
    expect(expandedSummary.match(/:z-index="3600"/g)).toHaveLength(6)
    expect(expandedSummary).toContain("t('protection.backupsPage.snapshotRecoverableDataHint')")
    expect(expandedSummary).toContain("t('protection.backupsPage.snapshotNewOriginalDataHint')")
    expect(expandedSummary).toContain("t('protection.backupsPage.snapshotNewStorageHint')")
    expect(expandedSummary).toContain("t('protection.backupsPage.snapshotDataReuseHint')")
    expect(expandedSummary).toContain("t('protection.backupsPage.snapshotCompressionSavingsHint')")
    expect(expandedSummary).toContain("t('protection.backupsPage.snapshotCombinedReductionHint')")
    expect(drawer).toContain('Math.max(0, Number(value))')
    expect(drawer).toContain('return `${value.toFixed(2)} : 1`')
    expect(enProtectionPages.backupsPage.snapshotListSize).toBe('Size')
    expect(enProtectionPages.backupsPage.snapshotRecoverableData).toBe('Restore Size')
    expect(enProtectionPages.backupsPage.snapshotNewOriginalData).toBe('New Data')
    expect(enProtectionPages.backupsPage.snapshotNewStorage).toBe('Snapshot Size')
    expect(enProtectionPages.backupsPage.snapshotDataReuse).toBe('Reuse Rate')
    expect(enProtectionPages.backupsPage.snapshotCompressionSavings).toBe('Compression Savings')
    expect(enProtectionPages.backupsPage.snapshotCombinedReduction).toBe('Reduction Ratio')
    expect(enProtectionPages.backupsPage.snapshotStorageFullyReused).toBe('Fully reused')
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

  it('keeps snapshot columns compact and places status before timestamps', () => {
    const snapshotTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabSnapshots\')" name="snapshots">',
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
    )

    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colSnapId\')" width="140" fixed>')
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colSnapStart\')" width="150">')
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colSnapEnd\')" width="150">')
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupsPage.snapshotListSize\')" width="88" align="right">')
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupsPage.snapshotRecoverableData\')" width="105" align="right">')
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.sourceResources.colActions\')" width="171"')
    expect(drawer).toContain('gap: 6px;')
    expect(drawer).toContain('padding: 0 3px;')
    expect(drawer).toContain('padding: 3px 8px;')
    expect(snapshotTab.indexOf("t('protection.backupDetail.labelStatus')"))
      .toBeLessThan(snapshotTab.indexOf("t('protection.backupDetail.colSnapStart')"))
  })

  it('keeps expanded snapshot details pinned to the main table viewport', () => {
    expect(drawer).toContain('class="hfl-list-table snapshot-points-table"')
    expect(drawer).toContain('.snapshot-points-table { container-type: inline-size; }')
    expect(drawer).toContain('.snapshot-directory-expand-panel { position: sticky; left: 35px;')
    expect(drawer).toContain('width: calc(100cqw - 49px);')
    expect(drawer).toContain('.snapshot-directory-table { width: 100%; min-width: 0; }')
  })

  it('keeps expanded restore details pinned to the drawer viewport', () => {
    expect(drawer).toContain('class="hfl-list-table restore-task-drawer-table restore-records-table"')
    expect(drawer).toContain('.restore-records-table { container-type: inline-size; }')
    expect(drawer).toContain('.restore-record-expand-panel { position: sticky; left: 35px;')
    expect(drawer).toContain('width: calc(100cqw - 49px);')
    expect(drawer).toContain('max-width: calc(100cqw - 49px); overflow-x: hidden; margin-left: 35px;')
  })

  it('keeps restore record headers aligned while horizontally scrolling resized columns', () => {
    const restoreTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
      '<el-tab-pane :label="t(\'protection.backupDetail.tabTasks\')" name="tasks">',
    )

    expect(restoreTab).toContain("v-table-column-resize=\"'protection.flowBackupSource.restoreRecords'\"")
    expect(restoreTab).toContain('v-table-header-scroll-sync')
    expect(restoreTab).toContain(':fit="false"')
  })

  it('updates running restore durations and stops the clock outside the active records tab', () => {
    const duration = sourceBetween(
      'function restoreRecordDuration(record: RestoreRecord)',
      'function restoreRecordConflictLabel',
    )
    const timer = sourceBetween(
      'function stopRestoreRecordDurationTimer()',
      'function syncRestoreRecordPolling()',
    )

    expect(duration).toContain("status === 'running'")
    expect(duration).toContain('restoreRecordDurationNow.value')
    expect(duration).toContain('record.task_summary?.finished_at')
    expect(timer).toContain("activeTab.value !== 'restoreRecords'")
    expect(timer).toContain('!hasRunningRestoreRecords.value')
    expect(timer).toContain('RESTORE_RECORD_DURATION_INTERVAL_MS')
    expect(drawer).toContain('stopRestoreRecordDurationTimer()')
  })

  it('adds a single restore record search field and ordered status filters without advanced filtering', () => {
    const restoreTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
      '<el-tab-pane :label="t(\'protection.backupDetail.tabTasks\')" name="tasks">',
    )
    const loader = sourceBetween(
      'async function loadRestoreRecordsForSource',
      'function stopRestoreRecordPolling()',
    )

    expect(restoreTab).toContain('v-model="restoreRecordFilterQuery"')
    expect(restoreTab).toContain('v-model="restoreRecordSearchField"')
    expect(restoreTab).not.toContain('v-model="restoreRecordSearchField" multiple')
    expect(restoreTab).toContain('@change="handleRestoreRecordSearchFieldChange"')
    expect(restoreTab).toContain('v-model="restoreRecordFilterStatus"')
    expect(restoreTab).toContain('v-model="restoreRecordFilterSourceMode"')
    expect(restoreTab).toContain('v-model="restoreRecordFilterTimeMode"')
    expect(restoreTab).toContain('@click="loadRestoreRecordsForSource()"')
    expect(restoreTab).not.toContain('advancedFilter')
    expect(loader).toContain('search_fields: restoreRecordSearchField.value')
    expect(loader).toContain('status: restoreRecordFilterStatus.value || undefined')
    expect(loader).toContain('source_mode: restoreRecordFilterSourceMode.value || undefined')
    expect(loader).toContain('created_from: createdRange.from')
    expect(loader).toContain('created_to: createdRange.to')
    expect(drawer).toContain("const restoreRecordSearchField = ref<RestoreRecordSearchField>('restore_uid')")
    expect(drawer).toContain("const RESTORE_RECORD_STATUS_OPTIONS = ['success', 'running', 'failed', 'cancelled', 'pending', 'timeout']")
    expect(restoreTab).toContain('v-for="option in restoreRecordStatusOptions"')
    expect(restoreTab).not.toContain('v-for="option in taskStatusOptions"')
    expect(en.ops.task.status.success).toBe('Succeeded')
    expect(en.ops.task.status.running).toBe('Running')
    expect(en.ops.task.status.failed).toBe('Failed')
    expect(en.ops.task.status.cancelled).toBe('Cancelled')
    expect(en.ops.task.status.pending).toBe('Queued')
    expect(en.ops.task.status.timeout).toBe('Timed out')
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
