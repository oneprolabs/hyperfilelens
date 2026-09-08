import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../../../locales/en'
import { enProtectionPages } from '../../../locales/enProtectionPages'
import { compactSourceText } from '../../../test/sourceText'

const drawer = compactSourceText(
  readFileSync(resolve(process.cwd(), 'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue'), 'utf8'),
)
const snapshotPanel = compactSourceText(
  readFileSync(resolve(process.cwd(), 'src/pages/protection/components/SnapshotPointDetailPanel.vue'), 'utf8'),
)

function sourceTextBetween(source: string, start: string, end: string) {
  const startIndex = source.indexOf(start)
  const endIndex = source.indexOf(end, startIndex)
  expect(startIndex).toBeGreaterThanOrEqual(0)
  expect(endIndex).toBeGreaterThan(startIndex)
  return source.slice(startIndex, endIndex)
}

function sourceBetween(start: string, end: string) {
  return sourceTextBetween(drawer, start, end)
}

describe('FlowBackupSourceDetailDrawer task columns', () => {
  it('removes Current Step and Progress and shows normalized task start and end times', () => {
    const tasksTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupDetail.tabTasks\')" name="tasks">',
      '<ElDrawer v-model="taskAdvancedFilterOpen"',
    )

    expect(tasksTab).not.toContain("t('protection.backupsPage.flowTaskColPhase')")
    expect(tasksTab).toContain('<el-table-column :label="t(\'ops.task.colName\')" width="275" fixed>')
    expect(tasksTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colTaskType\')" width="205">')
    expect(tasksTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colTaskStatus\')" width="115">')
    expect(tasksTab).not.toContain("t('protection.backupsPage.flowTaskColProgress')")
    expect(tasksTab).toContain('<el-table-column :label="t(\'ops.task.colTrigger\')" width="105">')
    expect(tasksTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colStart\')" min-width="160">')
    expect(tasksTab).toContain('formatNullableTime(row.started_at || row.created_at)')
    expect(tasksTab).toContain('<el-table-column :label="t(\'protection.backupDetail.colEnd\')" min-width="160">')
    expect(tasksTab).toContain('formatNullableTime(row.finished_at)')
    expect(tasksTab).not.toContain('formatNullableTime(row.finished_at || row.created_at)')

    expect(275 + 205 + 115 + 105 + 160 + 160).toBeLessThanOrEqual(1040)
  })

  it('keeps insight workspace restores out of the Protection task list', () => {
    const loader = sourceBetween(
      'async function loadTasksForSource()',
      'function refreshSourceDetailData()',
    )

    expect(loader).toContain("exclude_insight_workspace_restores: 'true'")
  })
})

describe('FlowBackupSourceDetailDrawer structured detail layout', () => {
  it('lets failure and skipped-item details use the unused event-time column', () => {
    expect(drawer).toContain("['failure_details', 'skipped_details']")
    expect(drawer).toContain("'dp-task-detail__event-row--detail-panel': hasEventDetailPanel(event)")
    expect(drawer).toContain('.dp-task-detail__event-row--detail-panel .dp-task-detail__event-content')
    expect(drawer).toContain('grid-template-columns: 16px minmax(0, 1fr);')
    expect(drawer).toContain('.dp-task-detail__event-row--detail-panel .dp-task-detail__event-time')
  })
})

describe('FlowBackupSourceDetailDrawer source status', () => {
  it('shows host system specifications in the overview', () => {
    const sourceInfo = sourceBetween(
      "<h4 class=\"hfl-detail-section__title\">{{ t('protection.backupsPage.flowSourceDetailSectionSpecs') }}</h4>",
      '</section></template>',
    )

    expect(drawer).toContain("v-if=\"overviewSource.type === 'host'\"")
    expect(sourceInfo).toContain("t('protection.sourceResources.colCpu')")
    expect(sourceInfo).toContain("t('protection.sourceResources.colMemory')")
    expect(sourceInfo).toContain("t('protection.sourceResources.colDiskCount')")
    expect(sourceInfo).toContain("t('protection.backupsPage.flowSourceDetailOsType')")
    expect(sourceInfo).toContain('<AgentPlatformBrandIcon')
    expect(sourceInfo).toContain('flowSourceOsPlatform(overviewSource)')
    expect(sourceInfo).toContain("t('protection.sourceResources.fieldArch')")
    expect(sourceInfo).toContain("t('protection.sourceResources.colCapacity')")
    expect(sourceInfo).toContain('flowSourceMemoryText(overviewSource)')
    expect(sourceInfo).toContain('flowSourceDiskCountText(overviewSource)')
    expect(drawer.indexOf('<section class="hfl-detail-section dp-flow-config-section">'))
      .toBeLessThan(drawer.indexOf("t('protection.backupsPage.flowSourceDetailSectionSpecs')"))
  })

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

describe('FlowBackupSourceDetailDrawer snapshot detail drawer', () => {
  it('adds snapshot filters and refresh without an advanced-filter entry', () => {
    const snapshotTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabSnapshots\')" name="snapshots">',
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
    )
    const loader = sourceBetween(
      'async function loadSnapshotsForSource()',
      'async function loadRestoreRecordsForSource',
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
      'function openSnapshotRestore',
    )

    expect(drawer).not.toContain('restoreBlockedByBackup?: boolean')
    expect(drawer).toContain('restoreBlockedByRestore?: boolean')
    expect(restoreGuard).toContain('!props.restoreBlockedByRestore && isSnapshotRestorable(row)')
    expect(restoreGuard).not.toContain('restoreBlockedByBackup')
  })

  it('shows compact storage metrics in the snapshot detail panel', () => {
    const snapshotTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabSnapshots\')" name="snapshots">',
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
    )
    const overview = sourceTextBetween(
      snapshotPanel,
      '<section class="snapshot-point-detail-section snapshot-point-detail-section--overview">',
      '<section class="snapshot-point-detail-section snapshot-point-detail-section--browser">',
    )

    expect(snapshotTab).toContain("t('protection.backupsPage.snapshotNewStorage')")
    expect(snapshotTab).toContain("t('protection.backupsPage.snapshotRecoverableData')")
    expect(snapshotTab).toContain('fmtReferenceBytes(row.new_packed_content_bytes)')
    expect(overview).not.toContain("t('protection.backupsPage.snapshotStorageEfficiencyTitle')")
    expect(overview).toContain('snapshot.new_original_content_bytes')
    expect(overview).toContain('snapshot.new_packed_content_bytes')
    expect(overview).toContain('snapshot.data_reuse_ratio')
    expect(overview).toContain('snapshot.compression_savings_ratio')
    expect(overview).toContain('fmtCombinedReduction(snapshot)')
    expect(overview.match(/<HflHelpTip/g)).toHaveLength(6)
    expect(overview.match(/popper-class="snapshot-metric-help-popper"/g)).toHaveLength(6)
    expect(overview).toContain("t('protection.backupsPage.snapshotRecoverableDataHint')")
    expect(overview).toContain("t('protection.backupsPage.snapshotNewOriginalDataHint')")
    expect(overview).toContain("t('protection.backupsPage.snapshotNewStorageHint')")
    expect(overview).toContain("t('protection.backupsPage.snapshotDataReuseHint')")
    expect(overview).toContain("t('protection.backupsPage.snapshotCompressionSavingsHint')")
    expect(overview).toContain("t('protection.backupsPage.snapshotCombinedReductionHint')")
    expect(snapshotPanel).toContain('Math.max(0, Number(value))')
    expect(snapshotPanel).toContain('return `${value.toFixed(2)} : 1`')
    expect(enProtectionPages.backupsPage.snapshotListSize).toBe('Size')
    expect(enProtectionPages.backupsPage.snapshotRecoverableData).toBe('Restore Size')
    expect(enProtectionPages.backupsPage.snapshotNewOriginalData).toBe('New Data')
    expect(enProtectionPages.backupsPage.snapshotNewStorage).toBe('Snapshot Size')
    expect(enProtectionPages.backupsPage.snapshotDataReuse).toBe('Reuse Rate')
    expect(enProtectionPages.backupsPage.snapshotCompressionSavings).toBe('Compression Savings')
    expect(enProtectionPages.backupsPage.snapshotCombinedReduction).toBe('Reduction Ratio')
    const snapshotMetricHints = [
      enProtectionPages.backupsPage.snapshotRecoverableDataHint,
      enProtectionPages.backupsPage.snapshotNewOriginalDataHint,
      enProtectionPages.backupsPage.snapshotNewStorageHint,
      enProtectionPages.backupsPage.snapshotDataReuseHint,
      enProtectionPages.backupsPage.snapshotCompressionSavingsHint,
      enProtectionPages.backupsPage.snapshotCombinedReductionHint,
    ]
    expect(snapshotMetricHints.every((hint) => !hint.includes('\n'))).toBe(true)
    expect(snapshotMetricHints.every((hint) => /\(.+\)/.test(hint))).toBe(true)
    expect(enProtectionPages.backupsPage.snapshotDataReuseHint).toContain('1 − New Data ÷ Restore Size')
    expect(enProtectionPages.backupsPage.snapshotCompressionSavingsHint).toContain('1 − Snapshot Size ÷ New Data')
    expect(enProtectionPages.backupsPage.snapshotCombinedReductionHint).toContain('Restore Size ÷ Snapshot Size')
    expect(enProtectionPages.backupsPage.snapshotStorageFullyReused).toBe('Fully reused')
  })

  it('opens the same snapshot detail drawer from the ID and Browse action', () => {
    const snapshotTab = sourceBetween(
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabSnapshots\')" name="snapshots">',
      '<el-tab-pane :label="t(\'protection.backupsPage.flowSourceDetailTabRestoreRecords\')" name="restoreRecords">',
    )
    expect(snapshotTab.match(/@click\.stop="openSnapshotDetailDrawer\(row\)"/g)).toHaveLength(2)
    expect(snapshotTab).toContain('<SnapshotPointDetailPanel')
    expect(snapshotTab).toContain("t('protection.backupsPage.snapshotBrowserBrowse')")
    expect(snapshotTab).not.toContain("t('protection.backupsPage.snapshotViewAction')")
    expect(snapshotTab).not.toContain('type="expand"')
    expect(snapshotTab).not.toContain(':expand-row-keys')
    expect(snapshotPanel).toContain('@click="browseSnapshotDirectory(directory)"')
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
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupsPage.snapshotNewStorage\')" width="128" align="right" label-class-name="hfl-table-no-tooltip"><template #header><span class="snapshot-point-table-header-with-tip"><span>{{ t(\'protection.backupsPage.snapshotNewStorage\') }}</span><HflHelpTip :content="t(\'protection.backupsPage.snapshotNewStorageHint\')"')
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.backupsPage.snapshotRecoverableData\')" width="125" align="right" label-class-name="hfl-table-no-tooltip"><template #header><span class="snapshot-point-table-header-with-tip"><span>{{ t(\'protection.backupsPage.snapshotRecoverableData\') }}</span><HflHelpTip :content="t(\'protection.backupsPage.snapshotRecoverableDataHint\')"')
    expect(snapshotTab.match(/<HflHelpTip/g)).toHaveLength(2)
    expect(snapshotTab.match(/popper-class="snapshot-metric-help-popper"/g)).toHaveLength(2)
    expect(snapshotTab).toContain('<el-table-column :label="t(\'protection.sourceResources.colActions\')" width="171"')
    expect(drawer).toMatch(/\.snapshot-point-actions__button--restore,\s*\.snapshot-point-actions__button--browse\s*{[^}]*color:\s*oklch\(51\.1% 0\.262 276\.966\);/s)
    expect(drawer).toContain(':global(.snapshot-metric-help-popper.el-popper)')
    expect(drawer).toContain('max-width: min(320px, calc(100vw - 32px)) !important;')
    expect(drawer).toContain('z-index: 3800 !important;')
    expect(drawer).toContain('white-space: normal;')
    expect(drawer).toContain('gap: 6px;')
    expect(drawer).toContain('padding: 0 3px;')
    expect(drawer).toContain('padding: 3px 8px;')
    expect(snapshotTab.indexOf("t('protection.backupDetail.labelStatus')"))
      .toBeLessThan(snapshotTab.indexOf("t('protection.backupDetail.colSnapStart')"))
  })

  it('uses a wider second-level drawer with internal scrolling', () => {
    expect(drawer).toContain('class="dp-snapshot-detail-drawer-shell"')
    expect(drawer).toContain('class="dp-snapshot-detail-drawer"')
    expect(drawer).toContain('role="dialog"')
    expect(drawer).toContain('aria-modal="true"')
    expect(drawer).toContain('const snapshotDetailDrawerSize = computed(() => {')
    expect(drawer).toContain('Math.min(desiredWidth, Math.max(280, outerWidth - 48))')
    expect(drawer).toContain(':style="{ width: snapshotDetailDrawerSize }"')
    expect(drawer).toContain('.dp-snapshot-detail-drawer__body { flex: 1; min-height: 0; overflow: hidden;')
    expect(snapshotPanel).toContain('grid-template-rows: 116px minmax(0, 1fr);')
    expect(snapshotPanel).toContain('grid-template-columns: repeat(3, minmax(150px, 1fr));')
    expect(snapshotPanel).toContain('.snapshot-point-detail-source-tree { min-width: 0; min-height: 0; overflow: auto;')
  })

  it('keeps source paths independently expandable and downloads one grouped selection', () => {
    expect(snapshotPanel).toContain('const browserStates = reactive(new Map<number, DirectoryBrowserState>())')
    expect(snapshotPanel).toContain('state.expanded = !state.expanded')
    expect(snapshotPanel).toContain('if (state.expanded && !state.loaded && !state.loading) void openDirectory(directory, state.path)')
    expect(snapshotPanel).toContain('v-for="directory in snapshotDirectories"')
    expect(snapshotPanel).toContain(':model-value="sourcePathChecked(directory)"')
    expect(snapshotPanel).toContain("state.rootChecked ? ['']")
    expect(snapshotPanel).toContain('createBackupSnapshotDownloadTask(props.snapshot.id, selectedDownloadGroups.value)')
    expect(snapshotPanel).toContain('projectedCount > maxSelectedItems.value')
    expect(snapshotPanel).toContain("apiError?.errorCode === 'PROTECTION.SNAPSHOT_MULTI_DOWNLOAD_UPGRADE_REQUIRED'")
    expect(snapshotPanel).toContain('pushToast({ message, type, title })')
    expect(snapshotPanel).toContain('background: #fff;')
    expect(snapshotPanel).toContain('background: rgb(241 245 249);')
    expect(snapshotPanel).toContain('z-index: 3800 !important;')
  })

  it('keeps expanded restore details pinned to the drawer viewport', () => {
    expect(drawer).toContain('class="hfl-list-table restore-task-drawer-table restore-records-table"')
    expect(drawer).toContain('.restore-records-table { container-type: inline-size; }')
    expect(drawer).toContain('.restore-record-expand-panel { position: sticky; left: 35px;')
    expect(drawer).toContain('width: calc(100cqw - 49px);')
    expect(drawer).toContain('max-width: calc(100cqw - 49px); overflow-x: hidden; margin-left: 35px;')
  })

  it('separates complete restore error codes from wrapped messages', () => {
    expect(drawer.match(/class="restore-record-structure-entry__error-code"/g)).toHaveLength(2)
    expect(drawer.match(/class="restore-record-structure-entry__error-message"/g)).toHaveLength(2)
    expect(drawer).toMatch(/\.restore-record-structure-entry__error\s*{[^}]*display:\s*grid;[^}]*color:\s*var\(--color-error-text\);/s)
    expect(drawer).toMatch(/\.restore-record-structure-entry__error-message\s*{[^}]*white-space:\s*pre-wrap;[^}]*overflow-wrap:\s*anywhere;/s)
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

    expect(duration).toContain("state.durationKind === 'running'")
    expect(duration).toContain('restoreRecordDurationNow.value')
    expect(duration).toContain('restoreRecordTimeState(record)')
    expect(duration).toContain("t('protection.backupDetail.durationDash')")
    expect(timer).toContain("activeTab.value !== 'restoreRecords'")
    expect(timer).toContain('!hasRunningRestoreRecords.value')
    expect(timer).toContain('RESTORE_RECORD_DURATION_INTERVAL_MS')
    expect(drawer).toContain('stopRestoreRecordDurationTimer()')
    expect(drawer).toContain("flowRestoreRecordSubmittedAt")
    expect(drawer).toContain("t('protection.backupDetail.colStart')")
    expect(drawer).toContain("t('protection.backupDetail.colEnd')")
    expect(drawer).not.toContain("t('protection.backupsPage.flowRestoreRecordFinishedAt')")
    expect(drawer).toContain("flowRestoreRecordTaskDetailsMissing")
    expect(drawer).toContain("flowRestoreRecordStartNotRecorded")
    expect(drawer).toContain("flowRestoreRecordFinishNotRecorded")
    expect(drawer).toContain("flowRestoreRecordInvalidTimeOrder")
    expect(drawer).toContain('isRestoreRecordActive(record)')
    expect(drawer).not.toContain('v-if="shouldShowRestoreRecordSubmittedAt(row)"')
    expect(drawer).toContain('class="restore-record-submitted-at"')
    expect(drawer).toContain('flowRestoreRecordNotStarted')
    expect(drawer).toContain('flowRestoreRecordNotFinished')
    expect(drawer).toContain('flowRestoreRecordTimeUnavailable')
    expect(drawer).toContain('class="restore-record-time-context__explanation"')
    expect(drawer).toContain('class="restore-record-time-summary__issue"')
    expect(drawer).toContain(':title="restoreRecordTimeIssue(row, \'started\')"')
    expect(drawer).toContain('trigger="click"')
    expect(drawer).toContain('restoreRecordEndTone(row)')
    expect(drawer).toContain("if (status === 'timeout') return TimerOff")
    expect(drawer).toContain('return CircleHelp')
    expect(drawer).toContain('restoreRecordTimeState(record).hasStatusTimeConflict')
    expect(drawer).toContain('restoreRecordTimeState(record).hasInvalidTimeData')
    expect(en.protection.backupsPage.flowRestoreRecordTimeIssueAria).toContain('{field}')
  })

  it('separates restore paths from endpoint metadata and uses semantic mapping icons', () => {
    expect(drawer).toContain('class="restore-record-mapping__content"')
    expect(drawer).toContain('class="restore-record-mapping__endpoint-meta"')
    expect(drawer).toContain('restoreRecordSourceEndpoint(row)')
    expect(drawer).toContain('restoreRecordTargetEndpoint(row)')
    expect(drawer).toContain('restoreItemTargetKind(row, item)')
    expect(drawer.match(/<ArrowRight :size="14" \/>/g)).toHaveLength(5)
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
    expect(drawer).toContain("const RESTORE_RECORD_STATUS_OPTIONS = ['success', 'running', 'failed', 'cancelled', 'pending', 'waiting', 'blocked', 'timeout']")
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
      'async function loadRestoreRecordsForSource',
    )

    expect(loader).not.toContain('snapshotDetails.value = new Map()')
    expect(loader).not.toContain('selectedSnapshotId.value = null')
  })

  it('hands ready snapshot artifacts to the browser without buffering a Blob', () => {
    const downloader = sourceTextBetween(
      snapshotPanel,
      'async function startNativeArtifactDownload',
      'async function downloadSelection',
    )

    expect(downloader).toContain('createSnapshotArtifactDownloadUrl(artifactId)')
    expect(downloader).toContain('anchor.href = result.url')
    expect(downloader).not.toContain('URL.createObjectURL')
    expect(downloader).not.toContain('.blob()')
  })

  it('loads snapshot directory pages on demand and explains partial results', () => {
    const pagination = sourceTextBetween(
      snapshotPanel,
      'function browserPageTreeNodes(',
      'function syncBrowserTreeCheckedKeys',
    )

    expect(pagination).toContain('result.has_more && result.next_cursor')
    expect(pagination).toContain('async function loadMoreBrowserTreeEntries')
    expect(pagination).toContain('cursor: data.nextCursor')
    expect(pagination).toContain('replaceBrowserLoadMoreNode')
    expect(pagination).toContain('browserTree(directory.id)?.insertBefore(replacement, data)')
    expect(pagination).toContain('browserTree(directory.id)?.remove(data)')
    expect(pagination).toContain('state.entries = [...state.entries, ...result.entries]')
    expect(snapshotPanel).toContain("t('protection.backupsPage.snapshotBrowserPartialCount', { n: data.loadedCount })")
    expect(snapshotPanel).toContain('@click.stop="loadMoreBrowserTreeEntries(directory, data)"')
    expect(enProtectionPages.backupsPage.snapshotBrowserPartialCount).toBe('{n} items loaded. More items are available.')
    expect(enProtectionPages.backupsPage.snapshotBrowserLoadMore).toBe('Load more')
  })

  it('closes the snapshot detail drawer when pagination changes', () => {
    const paginationWatcher = sourceBetween(
      '() => [snapshotPagination.page, snapshotPagination.pageSize] as const,',
      'watch(sourceId,',
    )

    expect(paginationWatcher).toContain('selectedSnapshotId.value = null')
    expect(paginationWatcher).toContain('snapshotDetails.value = new Map()')
    expect(paginationWatcher).toContain('resetSnapshotDetailDrawer()')
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
