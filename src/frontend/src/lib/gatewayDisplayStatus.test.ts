import { describe, expect, it, vi } from 'vitest'
import type { ApiNode } from '../types/node'
import {
  isGatewayConnectivityOnline,
  resolveAiEngineListStatus,
  resolveGatewayDisplayStatus,
  type GatewayAiPhase,
  type GatewayDisplayStatus,
} from './gatewayDisplayStatus'

const node = {} as ApiNode

function resolveFor(
  aiPhase: GatewayAiPhase,
  agentDisplay: GatewayDisplayStatus = {
    labelKey: 'nodeLifecycle.state.active',
    tagType: 'success',
  },
) {
  return resolveGatewayDisplayStatus(node, aiPhase, vi.fn(() => agentDisplay))
}

describe('resolveAiEngineListStatus', () => {
  it.each([
    ['online', 'insight.dataGateway.gatewayPhase.online', 'success', undefined],
    ['not_provisioned', 'insight.dataGateway.gatewayPhase.setup_incomplete', 'info', undefined],
    ['pending_install', 'insight.dataGateway.gatewayPhase.installing', 'info', true],
    ['agent_offline', 'insight.dataGateway.gatewayPhase.unavailable', 'info', undefined],
    ['offline', 'insight.dataGateway.gatewayPhase.degraded', 'danger', undefined],
    ['error', 'insight.dataGateway.gatewayPhase.error', 'danger', undefined],
  ] as const)(
    'maps %s to the AI Engine list label',
    (aiPhase, labelKey, tagType, spinning) => {
      expect(resolveAiEngineListStatus(aiPhase)).toEqual({
        labelKey,
        tagType,
        ...(spinning ? { spinning } : {}),
      })
    },
  )
})

describe('isGatewayConnectivityOnline', () => {
  it('prefers explicit availability when present', () => {
    expect(isGatewayConnectivityOnline({ availability: 'online', routable: false })).toBe(true)
    expect(isGatewayConnectivityOnline({ availability: 'offline', routable: true, sl_status: 'online' })).toBe(false)
  })

  it('falls back to routable then SL status when availability is omitted', () => {
    expect(isGatewayConnectivityOnline({ routable: true })).toBe(true)
    expect(isGatewayConnectivityOnline({ sl_status: 'online' })).toBe(true)
    expect(isGatewayConnectivityOnline({ lensnode_status: 'online' })).toBe(true)
    expect(isGatewayConnectivityOnline({ sl_runtime_status: 'online' })).toBe(true)
    expect(isGatewayConnectivityOnline({ routable: false, sl_status: 'offline' })).toBe(false)
  })
})

describe('resolveGatewayDisplayStatus', () => {
  it.each([
    ['online', 'insight.dataGateway.gatewayPhase.online', 'success', undefined],
    ['not_provisioned', 'insight.dataGateway.gatewayPhase.setup_incomplete', 'info', undefined],
    ['pending_install', 'insight.dataGateway.gatewayPhase.installing', 'info', true],
    ['agent_offline', 'protection.sourceResources.nodeStatusOffline', 'danger', undefined],
    ['offline', 'insight.dataGateway.gatewayPhase.degraded', 'danger', undefined],
    ['error', 'insight.dataGateway.gatewayPhase.error', 'danger', undefined],
  ] as const)(
    'shows the %s AI Engine phase for an active Gateway Agent',
    (aiPhase, labelKey, tagType, spinning) => {
      expect(resolveFor(aiPhase)).toEqual({ labelKey, tagType, ...(spinning ? { spinning } : {}) })
    },
  )

  it('keeps compatibility with the legacy online Agent status', () => {
    expect(resolveFor('error', {
      labelKey: 'protection.sourceResources.nodeStatusOnline',
      tagType: 'success',
    })).toEqual({
      labelKey: 'insight.dataGateway.gatewayPhase.error',
      tagType: 'danger',
    })
  })

  it('preserves an active lifecycle operation instead of replacing it with the AI Engine phase', () => {
    const lifecycleDisplay: GatewayDisplayStatus = {
      labelKey: 'nodeLifecycle.state.upgrading',
      tagType: 'info',
      spinning: true,
    }

    expect(resolveFor('error', lifecycleDisplay)).toBe(lifecycleDisplay)
  })

  it('preserves the SourceLens status for a Gateway that is not managed by HFL', () => {
    const externalNode = { managed_by_hfl: false } as ApiNode & { managed_by_hfl: boolean }
    const sourceLensDisplay: GatewayDisplayStatus = {
      labelKey: 'nodeLifecycle.state.active',
      tagType: 'success',
    }

    expect(resolveGatewayDisplayStatus(
      externalNode,
      'not_provisioned',
      vi.fn(() => sourceLensDisplay),
    )).toBe(sourceLensDisplay)
  })
})
