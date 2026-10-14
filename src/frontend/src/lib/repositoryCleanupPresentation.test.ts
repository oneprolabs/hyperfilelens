import { describe, expect, it } from 'vitest'

import { repositoryCleanupMessage, repositoryCleanupBlockerSummary } from './repositoryCleanupPresentation'

const messages: Record<string, string> = {
  'repositoriesPage.cleanupOwnershipUnverified':
    'HyperFileLens cannot verify repository data left by 1 backup source connection(s).',
}
const t = ((key: string, values?: Record<string, unknown>) =>
  messages[key]?.replace('{n}', String(values?.n ?? '')) ?? key) as never

describe('repositoryCleanupMessage', () => {
  it('presents ownership failures in backup-source terms', () => {
    const message = repositoryCleanupMessage({
      code: 'repository_ownership_unverified',
      detail: 'Repository has 1 physical location(s).',
      count: 1,
    }, t)

    expect(message).toContain('backup source')
    expect(message).not.toContain('physical location')
  })

  it('keeps the backend detail for unknown cleanup codes', () => {
    expect(repositoryCleanupMessage({ code: 'future_code', detail: 'Future detail' }, t))
      .toBe('Future detail')
  })
})

describe('Repository association blocker translations', () => {
  it.each([
    ['associated_sources', 'cleanupBlockedSources'],
    ['associated_snapshots', 'cleanupBlockedSnapshots'],
    ['associated_restore_plans', 'cleanupBlockedRestorePlans'],
    ['associated_restore_records', 'cleanupRetainedRestoreHistory'],
  ])('localizes %s using structured counts', (code, key) => {
    const translate = ((messageKey: string, values: Record<string, unknown>) => `${messageKey}:${values.n}`) as never
    expect(repositoryCleanupMessage({ code, count: 3, detail: 'English backend detail' }, translate))
      .toBe(`repositoriesPage.${key}:3`)
  })
})

describe('Association resource summary', () => {
  it('combines positive counts in stable order without absorbing other blockers', () => {
    const translate = ((key: string, values?: Record<string, unknown>) => {
      const copy: Record<string, string> = {
        'repositoriesPage.cleanupResourceSummary': '关联资源：{resources}',
        'repositoriesPage.cleanupResourceSeparator': ' · ',
        'repositoriesPage.cleanupResourceSources': '{n} 个备份源',
        'repositoriesPage.cleanupResourceSnapshots': '{n} 条快照记录',
        'repositoriesPage.cleanupResourceRestorePlans': '{n} 个恢复计划',
      }
      return (copy[key] || key).replace('{n}', String(values?.n)).replace('{resources}', String(values?.resources))
    }) as never
    const result = repositoryCleanupBlockerSummary([
      { code: 'associated_snapshots', count: 1, detail: 'snapshots' },
      { code: 'active_task', detail: 'Task still active' },
      { code: 'associated_restore_plans', count: 0, detail: 'plans' },
      { code: 'associated_sources', count: 1, detail: 'sources' },
    ], translate)
    expect(result.associationSummary).toBe('关联资源：1 个备份源 · 1 条快照记录')
    expect(result.blockers).toEqual(['Task still active'])
  })

  it('keeps unknown-count reasons instead of inventing a count', () => {
    const result = repositoryCleanupBlockerSummary([{ code: 'associated_sources', detail: 'Source count unavailable' }], t)
    expect(result.associationSummary).toBe('')
    expect(result.blockers).toEqual(['Source count unavailable'])
  })
})

describe('Legacy Local Disk data preservation warning', () => {
  it('uses localized copy rather than parsing an English detail or path', () => {
    const translate = ((key: string) => key === 'repositoriesPage.cleanupLegacyLocalDiskPreserved'
      ? '删除仓库记录后，物理数据仍会保留。' : key) as never
    expect(repositoryCleanupMessage({ code: 'legacy_local_disk_preserved', detail: 'Legacy path "/old/data" is retained.' }, translate))
      .toBe('删除仓库记录后，物理数据仍会保留。')
  })
})
