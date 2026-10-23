import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(): string {
  return readFileSync(resolve(process.cwd(), 'src/pages/insight/InsightAssistants.vue'), 'utf8')
}

describe('Admin Assistants list', () => {
  const page = source()

  it('uses Admin chrome for Add, More Actions, Organization, and empty state', () => {
    expect(page).toContain("t('platformOps.engineActions.addAssistant')")
    expect(page).toContain("t('platformOps.engineActions.assistantActions')")
    expect(page).toContain("t('insight.assistants.colOrganization')")
    expect(page).toContain('emptyPlatform')
    expect(page).not.toContain('isPlatformEngine')
  })

  it('does not show Visibility on the Admin inventory list', () => {
    expect(page).not.toContain("t('insight.assistants.colVisibility')")
  })

  it('keeps inventory columns to Name, Organization, Knowledge Source, Scenario, Status', () => {
    expect(page).toContain("t('insight.assistants.colName')")
    expect(page).toContain("t('insight.assistants.colOrganization')")
    expect(page).toContain("t('insight.assistants.colKnowledgeSource')")
    expect(page).toContain("t('insight.assistants.colScenario')")
    expect(page).toContain("t('insight.assistants.colStatus')")
    expect(page).not.toContain('insight-assistants-slug')
    expect(page).not.toContain('knowledgeSourceMeta')
    expect(page).toContain('filterOrgKey')
    expect(page).toContain("t('insight.assistants.orgFilterPlaceholder')")
    expect(page).toContain('organizationId')
    expect(page).toContain('`/platform-ops/orgs/${organizationId(row)}`')
  })

  it('paginates like Data Gateways and uses inventory Scenario titles', () => {
    expect(page).toContain('PlatformOpsPagination')
    expect(page).toContain('visibleRows')
    expect(page).toContain('selected_task_title')
    expect(page).not.toContain('fetchLensAssistantFormOptions')
  })

  it('confirms enable, disable, and delete with Users-style DangerConfirmDialog', () => {
    expect(page).toContain("requestLifecycleAction('enable'")
    expect(page).toContain("requestLifecycleAction('disable'")
    expect(page).toContain("requestLifecycleAction('delete'")
    expect(page).toContain("confirm-mode=\"lifecycleAction === 'delete' ? 'keyword' : 'single'\"")
    expect(page).toContain('lifecycleSubtitle')
    expect(page).toContain('organizationContext')
    expect(page).toContain('enableConfirmMessage')
    expect(page).toContain('disableConfirmWarning')
    expect(page).toContain('deleteConfirmWarning')
  })
})
