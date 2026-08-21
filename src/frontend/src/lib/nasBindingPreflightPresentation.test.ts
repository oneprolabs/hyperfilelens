import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import type { StorageRepositoryBindingPreflight } from './storageRepositoryApi'
import {
  bindingPreflightFromApiError,
  nasBindingPreflightPresentation,
} from './nasBindingPreflightPresentation'

const zhHans = JSON.parse(readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'),
  'utf8',
))

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: { en, 'zh-hans': zhHans },
})

function translate(locale: 'en' | 'zh-hans') {
  i18n.global.locale.value = locale
  return (key: string, params?: Record<string, unknown>) => i18n.global.t(key, params || {})
}

function preflight(
  blockerCode: string,
  overrides: Partial<StorageRepositoryBindingPreflight> = {},
): StorageRepositoryBindingPreflight {
  return {
    allowed: false,
    recovery_eligible: false,
    blocker_code: blockerCode,
    required_action: 'recreate_repository',
    claim_count: 1,
    claim_states: ['residual'],
    owners: [{
      node_id: 80,
      node_name: 'dev80',
      node_role: 'proxy',
      node_online: true,
      claim_state: 'residual',
    }],
    selected_proxy: {
      node_id: 24,
      node_name: 'proxy24',
      node_role: 'proxy',
    },
    confirmation_required: false,
    message: 'The retained physical target cannot be tied to a failed provisioning attempt.',
    ...overrides,
  }
}

describe('NAS binding preflight presentation', () => {
  it('guides Simplified Chinese users to clean associated backup sources', () => {
    const result = nasBindingPreflightPresentation(preflight(
      'DIRECT_NAS_BIND_DEPENDENCIES',
      {
        dependency_blockers: [{
          code: 'associated_sources',
          detail: 'Repository has 2 associated backup source(s).',
          count: 2,
        }],
      },
    ), translate('zh-hans'))

    expect(result.title).toBe(zhHans.repairNasRepo.bindingBlockedAssociatedTitle)
    expect(result.detail).toBe(
      zhHans.repairNasRepo.bindingBlockedAssociatedDetail.replace('{n}', '2'),
    )
  })

  it('describes previous direct use without exposing storage implementation terms', () => {
    const result = nasBindingPreflightPresentation(
      preflight('DIRECT_NAS_RESIDUAL_UNVERIFIED'),
      translate('zh-hans'),
    )

    expect(result.title).toBe(zhHans.repairNasRepo.bindingBlockedPreviousUseTitle)
    expect(result.detail).toBe(zhHans.repairNasRepo.bindingBlockedPreviousUseDetail)
    expect(result.relatedNode).toBe('')
    expect(JSON.stringify(result)).not.toMatch(/residual|Claim/i)
  })

  it.each([
    'DIRECT_NAS_BIND_DEPENDENCIES',
    'DIRECT_NAS_OWNER_UPGRADE_REQUIRED',
    'DIRECT_NAS_OWNER_UNAVAILABLE',
    'DIRECT_NAS_ACTIVE_TARGETS',
    'DIRECT_NAS_RESIDUAL_UNVERIFIED',
    'DIRECT_NAS_FAILED_PROVISIONING_RESIDUAL',
  ])('uses localized English copy for %s', (blockerCode) => {
    const result = nasBindingPreflightPresentation(preflight(blockerCode), translate('en'))

    expect(result.title).not.toBe('')
    expect(result.detail).not.toBe('')
    expect(result.title).not.toContain('repairNasRepo.')
    expect(result.detail).not.toContain('repairNasRepo.')
    expect(JSON.stringify(result)).not.toContain('The retained physical target')
    expect(JSON.stringify(result)).not.toMatch(/\bresidual\b|\bClaim\b/i)
  })

  it('shows only actionable node identity for node-specific blockers', () => {
    const result = nasBindingPreflightPresentation(
      preflight('DIRECT_NAS_OWNER_UNAVAILABLE'),
      translate('zh-hans'),
    )

    expect(result.relatedNode).toBe(
      zhHans.repairNasRepo.bindingRelevantNode.replace(
        '{node}',
        `${zhHans.repairNasRepo.nodeRoleProxy} dev80`,
      ),
    )
    expect(result.relatedNode).not.toContain('residual')
  })

  it('uses a localized safe fallback for unknown blocker codes', () => {
    const result = nasBindingPreflightPresentation(
      preflight('DIRECT_NAS_FUTURE_BLOCKER'),
      translate('zh-hans'),
    )

    expect(result.title).toBe(zhHans.repairNasRepo.bindingBlockedUnknownTitle)
    expect(result.detail).toBe(zhHans.repairNasRepo.bindingBlockedUnknownDetail)
    expect(result.detail).not.toContain('The retained physical target')
  })

  it('extracts structured binding metadata from a repair race error', () => {
    const blocker = preflight('DIRECT_NAS_OWNER_UNAVAILABLE')
    expect(bindingPreflightFromApiError({
      errorCode: 'STORAGE.NAS_BIND_BLOCKED',
      meta: { binding_blocker: blocker },
    })).toEqual(blocker)
    expect(bindingPreflightFromApiError({
      errorCode: 'VALIDATION.FAILED',
      meta: { binding_blocker: blocker },
    })).toBeNull()
  })
})
