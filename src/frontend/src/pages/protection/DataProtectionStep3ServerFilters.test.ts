import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compactSourceText } from '../../test/sourceText'


const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
const compactPage = compactSourceText(page)
const locale = readFileSync(resolve(process.cwd(), 'src/locales/enProtectionPages.ts'), 'utf8')

function sourceBetween(startMarker: string, endMarker: string) {
  const start = page.indexOf(startMarker)
  const end = page.indexOf(endMarker, start + startMarker.length)
  expect(start).toBeGreaterThan(-1)
  expect(end).toBeGreaterThan(start)
  return page.slice(start, end)
}

describe('Backup Wizard Step 3 server filters', () => {
  it('sends selected-field search and every quick/advanced filter to the server', () => {
    const load = sourceBetween('async function loadStep3Selectable', 'async function refreshStep3State')

    expect(load).toContain('search_field: step3SearchField.value')
    expect(load).toContain('source_name: step3AdvancedSourceName.value.trim() || undefined')
    expect(load).toContain('source_hostname: step3AdvancedHostname.value.trim() || undefined')
    expect(load).toContain('source_ip: step3AdvancedIp.value.trim() || undefined')
    expect(load).toContain('type: step3SourceType.value || undefined')
    expect(load).toContain('source_status: step3SourceStatus.value || undefined')
    expect(load).toContain('availability: step3Availability.value || undefined')
    expect(load).toContain('backup_task_status: step3BackupTaskStatus.value || undefined')
    expect(load).toContain('restore_task_status: step3RestoreTaskStatus.value || undefined')
    expect(load).toContain('backup_policy_id: step3BackupPolicyId.value || undefined')
    expect(load).toContain('file_filter_rule_id: step3FileFilterRuleId.value || undefined')
    expect(load).toContain('repository_id: step3RepositoryId.value || undefined')
  })

  it('does not filter the active Step 3 server page in the frontend', () => {
    const filtered = sourceBetween('const filteredStep3SourceList', 'const paginatedStep2SourceList')

    expect(filtered).toContain('if (flowMainStep.value === 2)')
    expect(filtered).toContain('step3SourceList.value')
    expect(filtered.indexOf('step3SourceList.value')).toBeLessThan(filtered.indexOf('flowStep3FiltersMatch'))
    expect(page).toContain(':total="step3SelectableCount"')
    expect(page).not.toContain(':total="step3SelectableCount || filteredStep3SourceList.length"')
  })

  it('resets page one and reloads when any Step 3 filter changes', () => {
    const watcher = sourceBetween(
      'step3SourceStatus.value,\n    step3Availability.value',
      'const STEP3_REFRESH_IDLE_MS',
    )

    expect(watcher).toContain('step3RepositoryId.value')
    expect(watcher).toContain('flowStep2Pager.page = 1')
    expect(watcher).toContain('void refreshFlowStepData(2)')
  })

  it('keeps Advanced Filters as drafts until Apply commits them', () => {
    const drawer = sourceBetween('<div class="flow-filter-drawer__body scrollbar">', '<template #footer>')
    const handlers = sourceBetween('function syncStep3AdvancedFilterDrafts', 'const filteredBackupSelectableRows')

    expect(drawer).toContain('v-model="draftStep3AdvancedSourceName"')
    expect(drawer).toContain('v-model="draftStep3SourceStatus"')
    expect(drawer).toContain('v-model="draftStep3Availability"')
    expect(handlers).toContain('function openAdvancedFilters()')
    expect(handlers).toContain('function resetStep3AdvancedFilterDrafts()')
    expect(handlers).toContain('function cancelAdvancedFilters()')
    expect(handlers).toContain('function applyAdvancedFilters()')
    expect(handlers).toContain('step3SourceStatus.value = draftStep3SourceStatus.value')
    expect(handlers).toContain('step3RepositoryId.value = draftStep3RepositoryId.value')
  })

  it('uses aligned Reset and Cancel buttons with compact label-control rows', () => {
    const start = compactPage.indexOf('<div class="flow-filter-drawer__footer">')
    const end = compactPage.indexOf('</template></el-drawer>', start)
    const footer = compactPage.slice(start, end)

    expect(start).toBeGreaterThan(-1)
    expect(end).toBeGreaterThan(start)
    expect(footer).toContain('<ElButton @click="resetStep3AdvancedFilterDrafts">')
    expect(footer).toContain('<ElButton @click="cancelAdvancedFilters">')
    expect(footer).not.toContain('text class="flow-filter-drawer__reset-btn"')
    expect(page).toContain('grid-template-columns: minmax(108px, 30%) minmax(0, 1fr)')
    expect(page).toContain('.flow-filter-form :deep(.el-form-item__label)')
  })

  it('clears the search input when changing search type and skips empty-query requests', () => {
    const handler = sourceBetween('function onStep3SearchFieldChange', 'function flowStep3FiltersMatch')

    expect(handler).toContain("const hasSearchCondition = taskSearchQuery.value.trim() !== '' || debouncedTaskSearchQuery.value.trim() !== ''")
    expect(handler).toContain('clearTimeout(taskSearchDebounceTimer)')
    expect(handler).toContain('taskSearchQuery.value = \'\'')
    expect(handler).toContain('flowStep2Pager.page = 1')
    expect(handler).toContain('if (!hasSearchCondition) return')
    expect(handler).toContain("debouncedTaskSearchQuery.value = ''")
    expect(handler).toContain('void refreshFlowStepData(2)')
    expect(handler).toContain('void refreshFlowStepData(1)')
    expect(handler).toContain('void loadBackupSelectable()')
  })

  it('shares the field search and Availability controls with Steps 1 and 2', () => {
    const toolbar = sourceBetween(
      '<div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">',
      '<div class="hfl-list-toolbar__utility">',
    )
    expect(toolbar).toContain('class="hfl-list-search hfl-list-search-group"')
    expect(toolbar).toContain('v-model="step3SearchField"')
    expect(toolbar).toContain('v-if="flowMainStep !== 2"')
    expect(toolbar).toContain('v-model="step3Availability"')
  })

  it('limits the first two advanced filter drawer steps to Source Conditions', () => {
    const drawer = sourceBetween('<div class="flow-filter-drawer__body scrollbar">', '<template #footer>')
    expect(drawer).toContain('flowFilterSectionSource')
    expect(drawer).toContain('<template v-if="flowMainStep === 2">')
    expect(drawer).toContain('flowFilterSectionBackup')
    expect(drawer).toContain('flowFilterSectionTarget')
    expect(drawer).toContain('step3BackupTask')
    expect(drawer).toContain('step3RestoreTask')
  })

  it('uses the Tasks-style field prefix and explains NAS Proxy semantics', () => {
    expect(page).toContain('v-model="step3SearchField"')
    expect(page).toContain('#prepend')
    expect(page).toContain("t('protection.backupsPage.step3NasProxyHelp')")
    expect(locale).toContain('For NAS sources, Hostname and IP identify the bound execution Proxy')
  })

  it('removes the Step 3 status quick-filter from the toolbar', () => {
    const toolbar = sourceBetween(
      '<div class="hfl-list-toolbar__right hfl-list-toolbar__right--mobile-split">',
      '<div class="hfl-list-toolbar__utility">',
    )

    expect(toolbar).not.toContain('v-model="step3SourceStatus"')
    expect(toolbar).toContain('v-model="step3Availability"')
    expect(toolbar).toContain('v-model="step3BackupTaskStatus"')
    expect(toolbar).toContain('v-model="step3RestoreTaskStatus"')
    expect(toolbar.indexOf('v-model="step3BackupTaskStatus"')).toBeLessThan(toolbar.indexOf('v-model="step3Availability"'))
    expect(toolbar.indexOf('v-model="step3RestoreTaskStatus"')).toBeLessThan(toolbar.indexOf('v-model="step3Availability"'))
    expect(page).not.toContain('step3RunningTask')
})

  it('keeps Source Status limited to persistent source lifecycle states', () => {
    const sourceStatusOptions = sourceBetween('const step3SourceStatusOptions', 'const step3AvailabilityOptions')
    const availabilityOptions = sourceBetween('const step3AvailabilityOptions', 'const step3BackupTaskStatusOptions')

    expect(sourceStatusOptions).toContain("value: 'active'")
    expect(sourceStatusOptions).toContain("value: 'inactive'")
    expect(sourceStatusOptions).toContain("value: 'error'")
    expect(sourceStatusOptions).toContain("value: 'removing'")
    expect(sourceStatusOptions).toContain("value: 'remove_failed'")
    expect(sourceStatusOptions).not.toContain("value: 'online'")
    expect(sourceStatusOptions).not.toContain("value: 'offline'")
    expect(sourceStatusOptions).not.toContain("value: 'reconnecting'")
    expect(availabilityOptions).toContain("value: 'online'")
    expect(availabilityOptions).toContain("value: 'offline'")
  })

  it('adds host/NAS type filtering and row-level draft clear actions', () => {
    const drawer = sourceBetween('<div class="flow-filter-drawer__body scrollbar">', '<template #footer>')

    expect(drawer).toContain('v-model="draftStep3SourceType"')
    expect(page).toContain("value: 'host' as const")
    expect(page).toContain("value: 'nas' as const")
    expect(drawer).toContain('class="flow-filter-nas-proxy-alert"')
    expect(drawer).toContain("@click=\"clearStep3AdvancedFilterDraft('sourceStatus')\"")
    expect(drawer).toContain('<BrushCleaning :size="15" />')
    expect(page).toContain('.flow-filter-control :deep(.el-input__wrapper)')
    expect(page).toContain('background: rgb(248 250 252)')
  })
})
