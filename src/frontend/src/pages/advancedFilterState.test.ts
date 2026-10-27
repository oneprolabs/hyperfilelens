import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compactSourceText } from '../test/sourceText'

function source(path: string) {
  return compactSourceText(readFileSync(resolve(process.cwd(), 'src/pages', path), 'utf8'))
}

describe('advanced-filter applied state', () => {
  it.each([
    ['node/Repositories.vue', 'repositoryTaskTimeMode', 'onRepositoryTaskTimeModeChange'],
    ['protection/components/FlowBackupSourceDetailDrawer.vue', 'taskFilterTimeMode', 'onSourceTaskTimeModeChange'],
  ])('does not commit custom mode through v-model in %s', (path, model, handler) => {
    const page = source(path)
    expect(page).toContain(`:model-value="${model}"`)
    expect(page).not.toContain(`v-model="${model}"`)
    expect(page).toContain(`@change="${handler}"`)
  })

  it.each([
    ['node/Repositories.vue', 'repositoryTaskAdvancedFilterDraft.dateRange'],
    ['protection/components/FlowBackupSourceDetailDrawer.vue', 'taskAdvancedFilterDraft.dateRange'],
  ])('keeps the Date range contract in %s', (path, model) => {
    const page = source(path)
    const start = page.indexOf(`v-model="${model}"`)
    expect(start).toBeGreaterThan(-1)
    const picker = page.slice(start, page.indexOf('/>', start))
    expect(picker).toContain('type="datetimerange"')
    expect(picker).not.toContain('value-format=')
  })

  it.each([
    ['ops/Tasks.vue', 'openAdvancedFilterDrawer', 'syncAdvancedFilterDraft()'],
    ['ops/Audit.vue', 'openAdvancedFilterDrawer', 'syncAdvancedFilterDraft()'],
    ['protection/DataProtection.vue', 'openAdvancedFilters', 'syncStep3AdvancedFilterDrafts()'],
    ['protection/components/FlowBackupSourceDetailDrawer.vue', 'openSourceTaskAdvancedFilterDrawer', 'syncSourceTaskAdvancedFilterDraft()'],
    ['node/Repositories.vue', 'openRepositoryTaskAdvancedFilter', 'repositoryTaskAdvancedFilterDraft.dateRange = repositoryTaskDateRange.value'],
  ])('restores applied conditions before opening %s', (path, openFunction, synchronization) => {
    expect(source(path)).toContain(`function ${openFunction}() { ${synchronization}`)
  })
})
