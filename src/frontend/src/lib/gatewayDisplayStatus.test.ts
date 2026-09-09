import { describe, expect, it, vi } from 'vitest'
import type { ApiNode } from '../types/node'
import {
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
