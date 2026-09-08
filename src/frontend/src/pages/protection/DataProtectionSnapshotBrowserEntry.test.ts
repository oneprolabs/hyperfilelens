import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { enProtectionPages } from '../../locales/enProtectionPages'

const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const drawer = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/FlowBackupSourceDetailDrawer.vue'),
  'utf8',
)
const browserPage = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupDataBrowser.vue'), 'utf8')
const downloadFeedback = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/snapshotDownloadFeedback.ts'),
  'utf8',
)
const downloadLimitsPopover = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/SnapshotDownloadLimitsPopover.vue'),
  'utf8',
)
const snapshotPanel = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/SnapshotPointDetailPanel.vue'),
  'utf8',
)
const router = readFileSync(resolve(process.cwd(), 'src/router/index.ts'), 'utf8')
const appShell = readFileSync(resolve(process.cwd(), 'src/app/layout/AppShell.vue'), 'utf8')

function sourceBetween(source: string, startMarker: string, endMarker: string) {
  const start = source.indexOf(startMarker)
  const end = source.indexOf(endMarker, start + startMarker.length)
  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return source.slice(start, end)
}

describe('Data Protection snapshot browser shortcut', () => {
  it('places an explicit snapshot action beside backup and restore', () => {
    const toolbar = sourceBetween(
      page,
      '<template v-else>\n                  <ElTooltip\n                    :content="t(\'protection.backupsPage.btnStartBackupCloudHint\')"',
      '<ElDropdown\n                    ref="flowMoreActionsDropdownRef"',
    )

    const backupAction = toolbar.indexOf("t('protection.backupsPage.btnStartBackup')")
    const restoreAction = toolbar.indexOf("t('protection.backupsPage.btnRecover')", backupAction)
    const browseAction = toolbar.indexOf("t('protection.backupsPage.snapshotBrowserOpen')", restoreAction)

    expect(backupAction).toBeGreaterThan(-1)
    expect(restoreAction).toBeGreaterThan(backupAction)
    expect(browseAction).toBeGreaterThan(restoreAction)
    expect(toolbar).toContain(':disabled="!step3SnapshotBrowserEnabled"')
    expect(toolbar).toContain('@click="openSelectedSnapshotFiles"')
    expect(toolbar).toContain('dp-flow-step3-action-btn--browse')
    expect(toolbar).toContain('dp-flow-step3-action-btn__icon')
    expect(page).toMatch(/\.dp-flow-step3-action-btn--browse\.el-button:not\(\.is-disabled\)\s*{[^}]*--color-border:\s*oklch\(87% 0\.065 274\.039\);[^}]*--color-text-title:\s*oklch\(51\.1% 0\.262 276\.966\);/s)
    expect(page).toMatch(/\.dp-flow-step3-action-btn--browse\.el-button:not\(\.is-disabled\)\s*{[^}]*border-color:\s*oklch\(87% 0\.065 274\.039\);[^}]*color:\s*oklch\(51\.1% 0\.262 276\.966\);/s)
    expect(page).toMatch(/\.dp-flow-step3-action-btn--browse\.el-button:not\(\.is-disabled\) \.dp-flow-step3-action-btn__icon\s*{[^}]*color:\s*oklch\(58\.5% 0\.233 277\.117\);/s)
    expect(page).toMatch(/#app \.dp-flow-step3-action-btn--browse\.el-button:not\(\.is-disabled\):hover,[^{]+\{[^}]*border-color:\s*oklch\(78\.5% 0\.115 274\.713\) !important;[^}]*background:\s*oklch\(96\.2% 0\.018 272\.314\) !important;[^}]*color:\s*oklch\(51\.1% 0\.262 276\.966\) !important;/s)
    expect(page).toMatch(/#app \.dp-flow-step3-action-btn--browse\.el-button:not\(\.is-disabled\):hover\s*{[^}]*border-color:\s*var\(--color-primary-hover, #4e3fd4\) !important;/s)
    expect(page).toMatch(/\.dp-flow-step3-action-btn--browse\.el-button\.is-disabled,[^{]+\{[^}]*border-color:\s*oklch\(92\.9% 0\.013 255\.508\);[^}]*background:\s*oklch\(98\.4% 0\.003 247\.858\);[^}]*color:\s*oklch\(70\.4% 0\.04 256\.788\);/s)
    expect(enProtectionPages.backupsPage.snapshotBrowserOpen).toBe('Browse backup data')
  })

  it('enables the shortcut only for one selected configured source', () => {
    expect(page).toContain(
      'const step3SnapshotBrowserEnabled = computed(() => step3SourceSelection.value.length === 1)',
    )
    expect(enProtectionPages.backupsPage.snapshotBrowserSelectOneSourceHint).toBe(
      'Select one configured backup source to browse its latest snapshot files.',
    )
  })

  it('opens a standalone backup data browser route without opening the detail drawer', () => {
    const handler = sourceBetween(
      page,
      'function openSelectedSnapshotFiles()',
      'watch(backupTaskDetailOpen',
    )

    expect(handler).toContain("name: 'protection-backup-data-browser'")
    expect(handler).toContain('params: { sourceType, sourceRefId }')
    expect(handler).not.toContain('openFlowSourceDetail(source)')
    expect(handler).not.toContain('openLatestSnapshotFiles')
  })
})

describe('Standalone backup data browser', () => {
  it('registers the standalone route before the dynamic backup detail route', () => {
    const browserRoute = router.indexOf("path: 'protection/backups/browse/:sourceType/:sourceRefId'")
    const detailRoute = router.indexOf("path: 'protection/backups/:backupId'")

    expect(browserRoute).toBeGreaterThan(-1)
    expect(detailRoute).toBeGreaterThan(browserRoute)
    expect(router).toContain("name: 'protection-backup-data-browser'")
    expect(router).toContain("import('../pages/protection/BackupDataBrowser.vue')")
  })

  it('keeps the browser page mounted while snapshot, directory, and path queries change', () => {
    expect(appShell).toContain("matchedRoute.name === 'protection-backup-data-browser'")
    expect(appShell).toContain('? matchedRoute.path')
    expect(browserPage).toContain('syncSnapshotRoute(row.id)')
    expect(browserPage).toContain('syncDirectoryRoute(directory.id, state.path)')
  })

  it('loads a paginated snapshot point list and restores the drawer selection from the URL', () => {
    expect(browserPage).toContain('listBackupSelectableSources({')
    expect(browserPage).toContain("expand: 'runtime'")
    expect(browserPage).toContain("ordering: '-created_at'")
    expect(browserPage).toContain('page: snapshotPagination.page')
    expect(browserPage).toContain('page_size: snapshotPagination.pageSize')
    expect(browserPage).toContain('include_directory_snapshots: 1')
    expect(browserPage).toContain('snapshotRows.value = result.results')
    expect(browserPage).toContain('const snapshotId = positiveQueryNumber(route.query.snapshot)')
    expect(browserPage).toContain(
      'const requestedDirectoryId = options.restoreSelection ? positiveQueryNumber(route.query.directory) : 0',
    )
    expect(browserPage).toContain("snapshot: String(activeSnapshotId.value)")
    expect(browserPage).toContain('path: path || undefined')
  })

  it('shows a loading indicator for Creating and Running snapshot states', () => {
    expect(enProtectionPages.backupsPage.snapshotStatusCreating).toBe('Creating')
    expect(enProtectionPages.backupsPage.snapshotStatusRunning).toBe('Running')

    for (const surface of [browserPage, drawer]) {
      expect(surface).toContain("normalized === 'running'")
      expect(surface).toContain("normalized === 'creating' || normalized === 'running'")
      expect(surface).toContain('<LoaderCircle')
      expect(surface).toContain('class="snapshot-status-tag__spinner"')
      expect(surface).toContain('animation: snapshot-status-spin 0.8s linear infinite;')
      expect(surface).toContain('@media (prefers-reduced-motion: reduce)')
    }
  })

  it('opens a two-region drawer with independently expandable source paths', () => {
    const switchDirectory = sourceBetween(
      browserPage,
      'function browseSnapshotDirectory(directory: BackupSourceSnapshotDirectory)',
      'async function openDirectory',
    )

    expect(browserPage).toContain(':page-title-override="t(\'protection.backupsPage.snapshotBrowserPageTitle\')"')
    expect(browserPage).toContain('class="fullscreen-form-header backup-data-browser-header"')
    expect(browserPage).toContain("t('protection.backupsPage.snapshotBrowserPageTitle')")
    expect(enProtectionPages.backupsPage.snapshotBrowserPageTitle).toBe('Browse backup Snapshot Points')
    expect(browserPage).toContain('@click="backToBackups"')
    expect(browserPage).toContain("query: { step: 'start-backup' }")
    expect(browserPage).toContain('@click.stop="openSnapshotDrawer(row)"')
    expect(browserPage).toContain('class="hfl-detail-drawer backup-data-snapshot-drawer"')
    expect(browserPage).toContain('backup-data-drawer-section--overview')
    expect(browserPage).not.toContain('backup-data-drawer-section--paths')
    expect(browserPage).toContain('backup-data-drawer-section--contents')
    expect(browserPage).toContain("t('protection.backupsPage.snapshotStorageEfficiencyTitle')")
    expect(browserPage).not.toContain("t('protection.backupsPage.snapshotBrowserProtectedPath')")
    const fileBrowserTitle = sourceBetween(
      browserPage,
      '<div class="backup-data-drawer-section__title backup-data-browser-files__title">',
      '</div>\n\n        <ElAlert',
    )
    expect(fileBrowserTitle).toContain("t('protection.backupsPage.snapshotBrowserPreviewTitle')")
    expect(fileBrowserTitle).toContain('class="backup-data-browser-files__actions"')
    expect(fileBrowserTitle).toContain("t('protection.backupsPage.snapshotBrowserSelectedCount'")
    expect(fileBrowserTitle).toContain('@click="clearDownloadSelection"')
    expect(fileBrowserTitle).toContain('@click="downloadSelection"')
    expect(browserPage).not.toContain('backup-data-browser-files__toolbar')
    expect(browserPage).toMatch(/\.backup-data-drawer-section--contents\s*\{[^}]*grid-template-rows:\s*auto auto minmax\(0, 1fr\) auto;/s)
    expect(browserPage).toContain('const directory = browsableDirectories.find')
    expect(browserPage).not.toContain('?? browsableDirectories[0]')
    expect(browserPage).toContain('if (!directory) return')
    expect(browserPage).toContain('window.requestAnimationFrame(() => {')
    expect(switchDirectory).toContain('const state = directoryBrowserState(directory)')
    expect(switchDirectory).toContain('state.expanded = false')
    expect(switchDirectory).toContain('if (!state.loaded && !state.loading) void openDirectory(directory, state.path)')
    expect(switchDirectory).toContain('syncSnapshotRoute(activeSnapshotId.value)')
    expect(switchDirectory).not.toContain('syncDirectoryRoute(')
    expect(switchDirectory).not.toContain('scheduleDirectoryLoad(directory)')
    expect(switchDirectory).not.toContain('loadSnapshotDetail')
    expect(browserPage).not.toContain('backup-data-browser-directory-select')
    expect(browserPage).toContain('class="backup-data-browser-source-tree"')
    expect(browserPage).toContain('class="backup-data-browser-source-tree__root-main"')
    expect(browserPage).toContain('class="backup-data-browser-source-tree__root-checkbox"')
    expect(browserPage).toContain(':model-value="sourcePathChecked(directory)"')
    expect(browserPage).toContain('@change="onSourcePathCheckChange(directory, $event)"')
    expect(browserPage).toContain("state.rootChecked\n      ? ['']")
    expect(browserPage).toContain('if (state.rootChecked) return true')
    expect(browserPage).toContain('state.rootChecked = false')
    expect(browserPage).toMatch(/\.backup-data-browser-files__title\s*\{[^}]*background:\s*rgb\(255 255 255\);/s)
    expect(browserPage).toMatch(/\.backup-data-browser-source-tree__header\s*\{[^}]*background:\s*rgb\(241 245 249\);/s)
    const sourcePathHeader = sourceBetween(
      browserPage,
      '<div class="backup-data-browser-source-tree__header">',
      '</div>\n\n          <div',
    )
    const sourcePathColumn = sourcePathHeader.indexOf("t('protection.backupDetail.colBackupDir')")
    const restoreSizeColumn = sourcePathHeader.indexOf("t('protection.backupsPage.snapshotRecoverableData')")
    const fileDirColumn = sourcePathHeader.indexOf("t('protection.backupsPage.snapshotBrowserFileDirCount')")
    const statusColumn = sourcePathHeader.indexOf("t('protection.backupDetail.labelStatus')")
    const selectedColumn = sourcePathHeader.indexOf("t('protection.backupsPage.snapshotBrowserSelected')")
    expect(sourcePathColumn).toBeGreaterThan(-1)
    expect(restoreSizeColumn).toBeGreaterThan(sourcePathColumn)
    expect(fileDirColumn).toBeGreaterThan(restoreSizeColumn)
    expect(statusColumn).toBeGreaterThan(fileDirColumn)
    expect(selectedColumn).toBeGreaterThan(statusColumn)
    expect(browserPage).toContain('fmtBytes(Number(directory.recoverable_size_bytes ?? directory.size_bytes ?? 0))')
    expect(browserPage).toContain('{{ directory.file_count }}/{{ directory.dir_count }}')
    expect(browserPage).toMatch(/\.backup-data-browser-source-tree__header,\s*\.backup-data-browser-source-tree__root\s*\{[^}]*grid-template-columns:\s*minmax\(360px, 3fr\)[^}]*minmax\(118px, 0\.7fr\)[^}]*104px[^}]*72px;/s)
    expect(browserPage).toContain('class="backup-data-browser-source-tree__header-status"')
    expect(browserPage).toMatch(/\.backup-data-browser-source-tree__header-status,\s*\.backup-data-browser-source-tree__header-selected\s*\{[^}]*text-align:\s*center;/s)
    expect(browserPage).toMatch(/\.backup-data-browser-source-tree__status\s*\{[^}]*justify-content:\s*center;/s)
    expect(browserPage).toMatch(/\.backup-data-browser-source-tree__path code\s*\{[^}]*overflow-wrap:\s*anywhere;[^}]*white-space:\s*normal;[^}]*word-break:\s*break-word;/s)
    expect(browserPage).toMatch(/\.backup-data-browser-files__title\s*\{[^}]*background:\s*rgb\(255 255 255\);/s)
    expect(browserPage).toMatch(/\.backup-data-browser-source-tree__header\s*\{[^}]*border-bottom:\s*1px solid rgb\(203 213 225\);[^}]*background:\s*rgb\(241 245 249\);/s)
    expect(browserPage).toContain('.backup-data-browser-source-tree__group:not(.is-disabled):not(.is-expanded)')
    expect(browserPage).toMatch(/\.backup-data-browser-source-tree__group\.is-expanded \.backup-data-browser-source-tree__root\s*\{[^}]*background:\s*rgb\(245 243 255\);/s)
    expect(browserPage).toMatch(/\.backup-data-browser-source-tree__children \.backup-data-browser-table__header\s*\{[^}]*background:\s*rgb\(255 255 255\);[^}]*font-weight:\s*500;/s)
    expect(browserPage).toContain('v-for="directory in selectedSnapshotDirectories"')
    expect(browserPage).toContain(':aria-expanded="directoryBrowserState(directory).expanded"')
    expect(browserPage).toContain('@click="browseSnapshotDirectory(directory)"')
    expect(browserPage).toContain(':disabled="!canBrowseDirectory(directory)"')
    expect(browserPage).toContain('<ChevronRight')
    expect(browserPage).toContain("t('protection.backupDetail.colBackupDir')")
    expect(browserPage).toContain("t('protection.backupDetail.labelStatus')")
    expect(browserPage).toContain('v-bind="lifecycleStatusTagAttrs(directory.status)"')
    expect(browserPage).toContain('v-if="!canBrowseDirectory(directory)"')
    expect(browserPage).toContain(':content="directoryBrowseUnavailableReason(directory)"')
    expect(browserPage).toContain('popper-class="snapshot-source-path-help-popper"')
    expect(browserPage).toContain('snapshotBrowserSnapshotStatusUnavailable')
    expect(browserPage).toContain('snapshotBrowserSourcePathStatusUnavailable')
    expect(browserPage).toContain('snapshotBrowserSourcePathSnapshotUnavailable')
    expect(browserPage).toContain('v-if="directoryBrowserState(directory).expanded"')
    expect(browserPage).toContain('const browserStates = reactive(new Map<number, DirectoryBrowserState>())')
    expect(browserPage).toContain('const browserTreeRefs = new Map<number, BrowserTreeInstance>()')
    expect(browserPage).toContain('const browserRequestControllers = new Map<string, AbortController>()')
    expect(browserPage).toContain('requests.registerCleanup(abortBrowserRequests)')
    expect(browserPage.match(/browseBackupSnapshotDirectory\([\s\S]*?\{ signal \}\)/g)).toHaveLength(3)
    expect(enProtectionPages.backupsPage.snapshotBrowserSourcePathStatusUnavailable).toContain('{status}')
    expect(enProtectionPages.backupsPage.snapshotBrowserSnapshotStatusUnavailable).toBe(
      'This source path cannot be browsed while the snapshot status is {status}.',
    )
    expect(enProtectionPages.backupsPage.snapshotReasonStatusUnavailable).toBe(
      'This snapshot cannot be restored while its status is {status}.',
    )
    expect(browserPage).toContain('z-index: 3600 !important;')
    expect(browserPage).toContain('v-if="selectedSnapshotDirectories.length"')
    expect(browserPage).not.toContain('v-for="entry in browserEntries"')
    expect(browserPage).toContain(':ref="(instance) => setBrowserTreeRef(directory.id, instance)"')
    expect(browserPage).toContain('show-checkbox')
    expect(browserPage).toContain('check-strictly')
    expect(browserPage).toContain(':load="(node, resolve) => loadBrowserTreeNode(directory, node, resolve)"')
    expect(browserPage).toContain('@check-change="(data, checked) => onBrowserTreeCheckChange(directory, data, checked)"')
    expect(browserPage).toContain('@click.stop="loadMoreBrowserTreeEntries(directory, data)"')
    expect(browserPage).toContain("t('protection.backupsPage.snapshotBrowserPath')")
    expect(browserPage).toContain('class="backup-data-browser-tree__row"')
    expect(browserPage).toContain('{{ data.path }}')
    expect(drawer).toContain('<SnapshotPointDetailPanel')
    expect(snapshotPanel).toContain(':load="(node, resolve) => loadBrowserTreeNode(directory, node, resolve)"')
    expect(snapshotPanel).toContain('@check-change="(data, checked) => onBrowserTreeCheckChange(directory, data, checked)"')
    expect(browserPage).toContain("t('protection.backupsPage.snapshotBrowserDownloadSelected')")
    expect(browserPage).toContain('createBackupSnapshotDownloadTask(snapshotId, selectedDownloadGroups.value)')
    expect(browserPage).toContain('projectedCount > snapshotDownloadMaxItems.value')
    expect(browserPage).toContain("downloadPhase.value = 'calculating'")
    expect(browserPage).toContain("downloadPhase.value = 'preparing'")
    expect(browserPage).toContain("t('protection.backupsPage.snapshotBrowserSelectionLimit', { n: snapshotDownloadMaxItems.value })")
    expect(browserPage).toContain("apiError?.errorCode === 'PROTECTION.SNAPSHOT_MULTI_DOWNLOAD_UPGRADE_REQUIRED'")
    expect(browserPage).toContain('showSnapshotDownloadError(error)')
    expect(browserPage).toContain('pushToast({')
    expect(browserPage).not.toContain('ElMessage')
    expect(browserPage).not.toContain('snapshot-browser-message')
    expect(browserPage).not.toContain('SNAPSHOT_BROWSER_MESSAGE_Z_INDEX')
    expect(drawer).not.toContain('ElMessage.error(')
    expect(drawer).not.toContain('ElMessage.warning(')
    expect(snapshotPanel).toContain('showDownloadError(error)')
    expect(snapshotPanel).toContain('pushToast({ message, type, title })')
    expect(downloadFeedback).toContain('PROTECTION.SNAPSHOT_DOWNLOAD_SIZE_LIMIT_EXCEEDED')
    expect(downloadFeedback).toContain('normalized.meta?.selected_size_bytes')
    expect(downloadFeedback).toContain('normalized.meta?.max_size_bytes')
    expect(downloadFeedback).toContain('/Selected data is (\\d+) bytes')
    expect(enProtectionPages.backupsPage.snapshotBrowserDownloadLimitTitle).toBe('Download limit exceeded')
    expect(enProtectionPages.backupsPage.snapshotBrowserDownloadLimitExceeded).toContain('{selected}')
    expect(enProtectionPages.backupsPage.snapshotBrowserDownloadLimitExceeded).toContain('{limit}')
    expect(browserPage).toContain('<SnapshotDownloadLimitsPopover')
    expect(snapshotPanel.match(/<SnapshotDownloadLimitsPopover/g)).toHaveLength(1)
    expect(browserPage).toContain('snapshotBrowserSelectedLimitCount')
    expect(snapshotPanel).toContain('snapshotBrowserSelectedLimitCount')
    expect(downloadLimitsPopover).toContain('popper-class="snapshot-download-limits-popper"')
    expect(downloadLimitsPopover).toContain('snapshotBrowserDownloadLimitsItemsValue')
    expect(downloadLimitsPopover).toContain('snapshotBrowserDownloadLimitsSizeValue')
    expect(downloadLimitsPopover).toContain('snapshotBrowserDownloadLimitsFoldersValue')
    expect(downloadLimitsPopover).toContain('snapshotBrowserDownloadLimitsRestoreHint')
    expect(downloadLimitsPopover).toContain('z-index: 3800 !important;')
    expect(enProtectionPages.backupsPage.snapshotBrowserDownloadLimitsFolders).toBe('Folder downloads')
    expect(enProtectionPages.backupsPage.snapshotBrowserDownloadLimitsFoldersValue).toContain('total download size')
    expect(enProtectionPages.backupsPage.snapshotBrowserSelectedLimitCount).toContain('{limit}')
    expect(enProtectionPages.backupsPage.snapshotBrowserDownloadLimitsRestoreHint).toContain('Restore')
    expect(browserPage).toContain("'protection.backupsPage.snapshotBrowserSourceUpgradeRequired'")
    expect(browserPage).toContain("'protection.backupsPage.snapshotBrowserNasProxyUpgradeRequired'")
    expect(enProtectionPages.backupsPage.snapshotBrowserSourceUpgradeRequired).toContain('single Source Path')
    expect(enProtectionPages.backupsPage.snapshotBrowserSourceUpgradeRequired).not.toContain('Agent')
    expect(enProtectionPages.backupsPage.snapshotBrowserNasProxyUpgradeRequired).toContain('NAS backup source proxy')
    expect(enProtectionPages.backupsPage.snapshotBrowserNasProxyUpgradeRequired).not.toContain('Agent')
    expect(browserPage).toContain("t('protection.backupsPage.snapshotBrowserClearSelection')")
    expect(browserPage).not.toContain("t('protection.backupsPage.snapshotBrowserReadOnly')")
    expect(browserPage).toContain('overflow: hidden;')
    expect(browserPage).toContain('grid-template-columns: repeat(6, minmax(120px, 1fr));')
    expect(browserPage).toContain('grid-template-rows: minmax(112px, 0.58fr) minmax(0, 3.53fr);')
    expect(browserPage.match(/<HflHelpTip/g)).toHaveLength(9)
    expect(browserPage.match(/popper-class="snapshot-metric-help-popper"/g)).toHaveLength(8)
    expect(browserPage).toContain(':global(.snapshot-metric-help-popper.el-popper)')
    expect(browserPage).toContain('z-index: 3600 !important;')
  })

  it('shows backup paths and preserves snapshot actions in the full-width list', () => {
    expect(browserPage).toContain('class="hfl-list-shell backup-data-snapshot-list"')
    expect(browserPage).not.toContain('backup-data-snapshot-list__title')
    expect(browserPage).toContain("t('protection.backupsPage.flowBackupColBackupDirs')")
    expect(browserPage).toContain(":label=\"t('protection.backupsPage.snapshotNewStorage')\"")
    expect(browserPage).not.toContain(":label=\"t('protection.backupsPage.snapshotListSize')\"")
    expect(browserPage.match(/label-class-name="hfl-table-no-tooltip"/g)).toHaveLength(2)
    expect(browserPage).toContain('class="snapshot-backup-path-preview"')
    expect(browserPage).toContain(':hide-after="BACKUP_PATH_POPOVER_HIDE_AFTER_MS"')
    expect(browserPage).toContain("t('protection.sourceResources.colActions')")
    expect(browserPage).toContain('width="200"')
    expect(browserPage.match(/width="160"/g)).toHaveLength(2)
    expect(browserPage).toContain('min-width="140"')
    expect(browserPage).toContain('min-width="360"')
    expect(browserPage).toContain('width="195"')
    expect(browserPage).toMatch(/:label="t\('protection\.backupsPage\.flowBackupColBackupDirs'\)"\s+min-width="360"\s+class-name="hfl-table-no-tooltip"/)
    expect(browserPage).toContain('class-name="hfl-table-actions-col hfl-table-no-tooltip"')
    expect(browserPage).toContain('@click.stop="openSnapshotRestore(row)"')
    expect(browserPage).toContain('@click.stop="openSnapshotDrawer(row)"')
    expect(browserPage).not.toContain(":title=\"t('protection.backupsPage.snapshotRecoverAction')\"")
    expect(browserPage).not.toContain(":title=\"t('protection.backupsPage.snapshotBrowserBrowse')\"")
    expect(browserPage).toMatch(/\.snapshot-point-actions__button--restore,\s*\.snapshot-point-actions__button--browse\s*{[^}]*color:\s*oklch\(51\.1% 0\.262 276\.966\);/s)
    expect(browserPage).toMatch(/\.snapshot-point-actions__button--restore:not\(:disabled\):hover,\s*\.snapshot-point-actions__button--browse:not\(:disabled\):hover\s*{[^}]*background:\s*oklch\(96\.2% 0\.018 272\.314\);/s)
    expect(browserPage).toContain("name: 'protection-snapshot-restore'")
    expect(browserPage).toContain('runtime?.running === true || runtime?.stopping === true')
  })

  it('opens the second-level browser from both snapshot list entry points', () => {
    expect(drawer.match(/@click\.stop="openSnapshotDetailDrawer\(row\)"/g)).toHaveLength(2)
    expect(drawer).toContain("t('protection.backupsPage.snapshotBrowserBrowse')")
    expect(drawer).toContain('<SnapshotPointDetailPanel')
    expect(drawer).not.toContain('@click.stop="toggleSnapshot(row)"')
    expect(drawer).not.toContain(':expand-row-keys="expandedSnapshotRowKeys"')
    expect(snapshotPanel).toContain('@click="browseSnapshotDirectory(directory)"')
    expect(drawer).not.toContain('snapshotBrowserOpenedFromShortcut')
    expect(drawer).not.toContain('openLatestSnapshotFiles')
    expect(enProtectionPages.backupsPage.snapshotBrowserOpen).toBe('Browse backup data')
    expect(enProtectionPages.backupsPage.snapshotBrowserPreviewTitle).toBe('File and Directory Browser')
    expect(enProtectionPages.backupsPage.snapshotBrowserReadOnly).toBe('read-only')
  })
})
