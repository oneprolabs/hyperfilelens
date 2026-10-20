// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { readStoredOrganizationKey } from './organizationStorage'
import { asyncNoticeScope } from './asyncNoticeLedger'
import {
  markWizardPendingBySourceIds, readWizardPendingSourceOps,
} from '../pages/protection/composables/backupWizardPendingStorage'

afterEach(() => { localStorage.clear(); sessionStorage.clear() })

describe('tab organization isolation', () => {
  it('keeps headers, pending operations, and notices in the same pinned organization', () => {
    localStorage.setItem('hfl_org_key', 'organization-a')
    sessionStorage.setItem('hfl_org_key', 'organization-a')
    markWizardPendingBySourceIds(['agent:9'], { kind: 'deleting', taskUuid: 'org-a-task' })
    // Another tab chooses organization B while this tab remains in A.
    localStorage.setItem('hfl_org_key', 'organization-b')
    expect(readStoredOrganizationKey()).toBe('organization-a')
    expect(asyncNoticeScope()).toBe('organization-a')
    expect(readWizardPendingSourceOps().get('agent:9')?.taskUuid).toBe('org-a-task')
    sessionStorage.setItem('hfl_org_key', 'organization-b')
    expect(readWizardPendingSourceOps().size).toBe(0)
  })

  it('uses the existing default when this tab has not selected an organization', () => {
    localStorage.setItem('hfl_org_key', 'default-org')
    expect(readStoredOrganizationKey()).toBe('default-org')
  })
})
