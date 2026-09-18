import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(): string {
  return readFileSync(resolve(process.cwd(), 'src/pages/insight/InsightKnowledgeBase.vue'), 'utf8')
}

describe('Admin Knowledge Sources list', () => {
  const page = source()

  it('uses Admin chrome for Organization, Add, and empty state', () => {
    expect(page).toContain("t('insight.kb.colOrganization')")
    expect(page).toContain("t('platformOps.engineActions.addKnowledgeSource')")
    expect(page).toContain('emptyPlatform')
    expect(page).not.toContain('isPlatformEngine')
  })

  it('uses Snapshot, Indexed Content, and Status column keys', () => {
    expect(page).toContain("t('insight.kb.colLinkedVersion')")
    expect(page).toContain("t('insight.kb.colRetrieval')")
    expect(page).toContain("t('insight.kb.colLearnStatus')")
  })

  it('paginates like Data Gateways', () => {
    expect(page).toContain('PlatformOpsPagination')
    expect(page).toContain('visibleRows')
  })

  it('confirms deletes with organization context', () => {
    expect(page).toContain('deleteConfirmPlatform')
    expect(page).toContain('organizationLabel')
  })
})
