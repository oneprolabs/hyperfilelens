import type { ApiNode } from '../types/node'

export type GatewayAiPhase =
  | 'not_provisioned'
  | 'pending_install'
  | 'online'
  | 'agent_offline'
  | 'offline'
  | 'error'

type GatewayStatusNode = ApiNode & {
  managed_by_hfl?: boolean
}

export type GatewayDisplayStatus = {
  labelKey: string
  tagType: 'success' | 'warning' | 'danger' | 'info'
  tagClass?: string
  spinning?: boolean
}

const AGENT_READY_KEYS = new Set([
  'nodeLifecycle.state.active',
  'protection.sourceResources.nodeStatusOnline',
])

export function resolveGatewayDisplayStatus(
  node: GatewayStatusNode,
  aiPhase: GatewayAiPhase,
  resolveDisplayStatus: (node: ApiNode) => GatewayDisplayStatus,
): GatewayDisplayStatus {
  const agentDisplay = resolveDisplayStatus(node)
  if (node.managed_by_hfl === false || !AGENT_READY_KEYS.has(agentDisplay.labelKey)) {
    return agentDisplay
  }

  switch (aiPhase) {
    case 'online':
      return { labelKey: 'insight.dataGateway.gatewayPhase.online', tagType: 'success' }
    case 'not_provisioned':
      return { labelKey: 'insight.dataGateway.gatewayPhase.setup_incomplete', tagType: 'info' }
    case 'pending_install':
      return {
        labelKey: 'insight.dataGateway.gatewayPhase.installing',
        tagType: 'info',
        spinning: true,
      }
    case 'agent_offline':
      return { labelKey: 'protection.sourceResources.nodeStatusOffline', tagType: 'danger' }
    case 'offline':
      return { labelKey: 'insight.dataGateway.gatewayPhase.degraded', tagType: 'danger' }
    case 'error':
      return { labelKey: 'insight.dataGateway.gatewayPhase.error', tagType: 'danger' }
    default:
      return agentDisplay
  }
}
