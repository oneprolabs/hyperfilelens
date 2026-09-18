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

/** List/detail status for the AI Engine column (LensNode sidecar), independent of Agent lifecycle. */
export function resolveAiEngineListStatus(aiPhase: GatewayAiPhase): GatewayDisplayStatus {
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
      // Agent is down — do not claim the AI Engine itself failed; Connectivity covers Agent reachability.
      return { labelKey: 'insight.dataGateway.gatewayPhase.unavailable', tagType: 'info' }
    case 'offline':
      return { labelKey: 'insight.dataGateway.gatewayPhase.degraded', tagType: 'danger' }
    case 'error':
      return { labelKey: 'insight.dataGateway.gatewayPhase.error', tagType: 'danger' }
    default:
      return { labelKey: 'insight.dataGateway.gatewayPhase.setup_incomplete', tagType: 'info' }
  }
}

/** Host connectivity for gateway list rows (handles SL-only rows that omit availability). */
export function isGatewayConnectivityOnline(row: {
  availability?: string | null
  routable?: boolean | null
  sl_status?: string | null
  lensnode_status?: string | null
  sl_runtime_status?: string | null
}): boolean {
  if (row.availability === 'online') return true
  if (row.availability === 'offline') return false
  if (row.routable === true) return true
  const slStatus = row.sl_runtime_status || row.sl_status || row.lensnode_status
  return slStatus === 'online'
}

export function resolveGatewayDisplayStatus(
  node: GatewayStatusNode,
  aiPhase: GatewayAiPhase,
  resolveDisplayStatus: (node: ApiNode) => GatewayDisplayStatus,
): GatewayDisplayStatus {
  const agentDisplay = resolveDisplayStatus(node)
  if (node.managed_by_hfl === false || !AGENT_READY_KEYS.has(agentDisplay.labelKey)) {
    return agentDisplay
  }

  if (aiPhase === 'agent_offline') {
    return { labelKey: 'protection.sourceResources.nodeStatusOffline', tagType: 'danger' }
  }
  return resolveAiEngineListStatus(aiPhase)
}
