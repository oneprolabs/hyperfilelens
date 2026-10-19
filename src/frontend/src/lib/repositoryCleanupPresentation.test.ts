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

  it('uses the existing residual guidance when a historical removal needs Force Delete', () => {
    const translate = ((key: string) => key) as never
    expect(repositoryCleanupMessage({
      code: 'removed_repository_requires_force',
      detail: 'Use Force Delete.',
    }, translate)).toBe('repositoriesPage.residualAttentionDescription')
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
        'repositoriesPage.cleanupResourceSummary': 'Associated resources: {resources}',
        'repositoriesPage.cleanupResourceSeparator': ' · ',
        'repositoriesPage.cleanupResourceSources': '{n} backup sources',
        'repositoriesPage.cleanupResourceSnapshots': '{n} snapshot records',
        'repositoriesPage.cleanupResourceRestorePlans': '{n} restore plans',
      }
      return (copy[key] || key).replace('{n}', String(values?.n)).replace('{resources}', String(values?.resources))
    }) as never
    const result = repositoryCleanupBlockerSummary([
      { code: 'associated_snapshots', count: 1, detail: 'snapshots' },
      { code: 'active_task', detail: 'Task still active' },
      { code: 'associated_restore_plans', count: 0, detail: 'plans' },
      { code: 'associated_sources', count: 1, detail: 'sources' },
    ], translate)
    expect(result.associationSummary).toBe('Associated resources: 1 backup sources · 1 snapshot records')
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
      ? 'Physical data is preserved after deletion.' : key) as never
    expect(repositoryCleanupMessage({ code: 'legacy_local_disk_preserved', detail: 'Legacy path "/old/data" is retained.' }, translate))
      .toBe('Physical data is preserved after deletion.')
  })
})
