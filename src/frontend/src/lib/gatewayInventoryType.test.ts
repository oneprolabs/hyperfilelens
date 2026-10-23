import { describe, expect, it } from 'vitest'
import { gatewayInventoryType } from './gatewayInventoryType'

describe('gatewayInventoryType', () => {
  it('classifies private organization and legacy user scopes', () => {
    expect(gatewayInventoryType({ scope: 'organization' })).toBe('private')
    expect(gatewayInventoryType({ scope: 'user' })).toBe('private')
  })

  it('classifies installer-managed public gateways as bundled', () => {
    expect(gatewayInventoryType({
      scope: 'platform',
      metadata: { managed_by: 'hfl-installer' },
    })).toBe('bundled')
  })

  it('classifies other public and unlinked rows as separately installed', () => {
    expect(gatewayInventoryType({ scope: 'platform', metadata: {} })).toBe('installed')
    expect(gatewayInventoryType({ scope: '', metadata: null })).toBe('installed')
  })
})
