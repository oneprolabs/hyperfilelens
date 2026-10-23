import type { GatewayAiStatus, LensGatewayInsight } from '../../lib/lensApi'

/** Build AI Engine drawer payload from directory / list insight fields. */
export function gatewayAiFromInsight(
  row: Pick<
    LensGatewayInsight,
    | 'id'
    | 'ai_enabled'
    | 'sl_lensnode_uuid'
    | 'sidecar_status'
    | 'workspace_root'
    | 'sl_name'
    | 'sl_status'
    | 'sl_workspace_path'
    | 'sl_agent_version'
    | 'sl_last_heartbeat_at'
    | 'sl_registered_at'
    | 'sl_tasks'
  >,
): GatewayAiStatus {
  return {
    gateway_id: row.id,
    ai_enabled: Boolean(row.ai_enabled ?? row.sl_lensnode_uuid),
    sl_lensnode_uuid: row.sl_lensnode_uuid ?? null,
    sidecar_status: row.sidecar_status || 'not_deployed',
    workspace_root: row.workspace_root || row.sl_workspace_path || '',
    sl_name: row.sl_name,
    sl_status: row.sl_status,
    sl_workspace_path: row.sl_workspace_path,
    sl_agent_version: row.sl_agent_version,
    sl_last_heartbeat_at: row.sl_last_heartbeat_at,
    sl_registered_at: row.sl_registered_at,
    sl_tasks: row.sl_tasks,
  }
}
