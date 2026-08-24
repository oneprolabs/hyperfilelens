import { describe, expect, it } from 'vitest'

import { repositoryCleanupMessage } from './repositoryCleanupPresentation'

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
