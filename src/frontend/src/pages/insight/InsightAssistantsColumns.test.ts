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

  it('paginates like Data Gateways and uses inventory Scenario titles', () => {
    expect(page).toContain('PlatformOpsPagination')
    expect(page).toContain('visibleRows')
    expect(page).toContain('selected_task_title')
    expect(page).not.toContain('fetchLensAssistantFormOptions')
  })

  it('confirms deletes with organization context', () => {
    expect(page).toContain('deleteConfirmPlatform')
    expect(page).toContain('organizationLabel')
  })
})
