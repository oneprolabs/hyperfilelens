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

  it('links Organization to org detail without showing the org key', () => {
    expect(page).toContain('organizationId')
    expect(page).toContain('`/platform-ops/orgs/${organizationId(row)}`')
    expect(page).toContain("row.organization_name?.trim() || ''")
    expect(page).not.toContain('`${name} (${key})`')
    expect(page).not.toContain('return name || key')
  })

  it('uses Snapshot, Indexed Content, and Status column keys', () => {
    expect(page).toContain("t('insight.kb.colLinkedVersion')")
    expect(page).toContain("t('insight.kb.colRetrieval')")
    expect(page).toContain("t('insight.kb.colLearnStatus')")
  })

  it('keeps Name single-line and adds Source Path plus Organization filter', () => {
    expect(page).toContain("t('insight.kb.colSourcePath')")
    expect(page).toContain('sourcePathLabel')
    expect(page).toContain('filterOrgKey')
    expect(page).toContain("t('insight.kb.orgFilterPlaceholder')")
    expect(page).not.toContain('ks-identity')
  })

  it('paginates like Data Gateways', () => {
    expect(page).toContain('PlatformOpsPagination')
    expect(page).toContain('visibleRows')
  })

  it('confirms Sync and Delete with Data Gateway-style danger dialogs', () => {
    expect(page).toContain('deleteConfirmPlatform')
    expect(page).toContain('syncConfirmMessage')
    expect(page).toContain("confirm-mode=\"actionType === 'delete' ? 'keyword' : 'single'\"")
    expect(page).toContain('common.deleteKeyword')
    expect(page).toContain('insight.kb.entityName')
    expect(page).toContain('requestAction')
  })
})
