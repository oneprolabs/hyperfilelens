import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const wizard = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'),
  'utf8',
)

describe('backup source browser comparison prototype', () => {
  it('keeps tree browsing as the default and exposes the layered alternative', () => {
    expect(wizard).toContain("type SourceBrowserMode = 'tree' | 'layered'")
    expect(wizard).toContain("return sourceBrowserModeBySource[sourceId] || 'tree'")
    expect(wizard).toContain("value: 'tree'")
    expect(wizard).toContain("value: 'layered'")
    expect(wizard).toContain('sourceBrowserMode(row.id) === \'tree\'')
  })

  it('uses the real directory API for one-level navigation and pagination', () => {
    expect(wizard).toContain('async function loadLayeredSourceDirectory')
    expect(wizard).toContain('await listRealSourceDirChildren(sourceId, path')
    expect(wizard).toContain('async function loadMoreLayeredSourceEntries')
    expect(wizard).toContain('openLayeredSourceBreadcrumb(row.id, index)')
    expect(wizard).toContain('openLayeredSourceDirectory(row.id, entry)')
  })

  it('shares pending selections and add modes between both browsers', () => {
    expect(wizard).toContain(':model-value="createSourceCheckedKeys(row.id).includes(entry.path)"')
    expect(wizard).toContain('onSourceDirCheckChange(row.id, entry, Boolean(checked))')
    expect(wizard).toContain('addSourceDirectoryByMode(row.id, entry, mode)')
    expect(wizard).toContain('addPickedSourceFor(row.id)')
  })

  it('loads every page before selecting the current level', () => {
    const selectAllBlock = wizard.slice(
      wizard.indexOf('async function loadAllLayeredSourceEntries'),
      wizard.indexOf('async function toggleLayeredCurrentLevel'),
    )
    expect(selectAllBlock).toContain('while (hasMore && cursor && !seenCursors.has(cursor))')
    expect(selectAllBlock).toContain('cursor,')
    expect(selectAllBlock).toContain('state.entries = entries')
  })

  it('keeps the prototype state isolated from persisted backup scope data', () => {
    expect(wizard).toContain('const sourceBrowserModeBySource = reactive')
    expect(wizard).toContain('const layeredSourceBrowserBySource = reactive')
    expect(wizard).toContain('delete sourceBrowserModeBySource[key]')
    expect(wizard).toContain('delete layeredSourceBrowserBySource[key]')
  })
})
