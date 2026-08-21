import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const source = readFileSync(
  resolve(process.cwd(), 'src/pages/node/RepairNasRepository.vue'),
  'utf8',
)

describe('Edit NAS Repository residual recovery', () => {
  it('preflights the selected Proxy before sending the repair request', () => {
    expect(source.indexOf('preflightStorageRepositoryBinding(')).toBeGreaterThan(-1)
    expect(source.indexOf('preflightStorageRepositoryBinding(')).toBeLessThan(
      source.indexOf('repairStorageRepository(repoId.value, payload)'),
    )
  })

  it('requires an explicit destructive confirmation for eligible cleanup and bind', () => {
    expect(source).toContain('if (!preflight.recovery_eligible)')
    expect(source).toContain('<DangerConfirmDialog')
    expect(source).toContain('confirm-mode="keyword"')
    expect(source).toContain('confirm-keyword="CLEAN UP AND BIND"')
    expect(source).toContain('await requestCleanupBindConfirmation(preflight.claim_count)')
    expect(source).toContain('payload.cleanup_failed_provisioning_targets = true')
    expect(source).toContain("payload.cleanup_confirmation = 'CLEAN UP AND BIND'")
  })

  it('renders localized blocker guidance instead of backend diagnostics or Claim state', () => {
    expect(source).toContain('nasBindingPreflightPresentation(')
    expect(source).toContain("t('repairNasRepo.bindingBlockedAssociatedDetail'")
    expect(source).toContain('bindingPresentation?.title')
    expect(source).toContain('bindingPresentation.detail')
    expect(source).not.toContain('bindingPreflight?.message')
    expect(source).not.toContain('preflight.message')
    expect(source).not.toContain('owner.claim_state')
    expect(source).not.toContain('retainedTargetOwners')
  })
})
