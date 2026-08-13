import { getNode } from './nodeApi'
import { getSourceResource } from './sourceApi'
import type { TaskResourceRow, TaskRow } from './taskApi'
import type { FlowSourceRow } from '../pages/protection/composables/useFlowSourceAggregate'

export type TaskBackupSourceResourceDisplay = {
  backupSource: string
  endpointName: string
  endpointIp: string
  registeredAt: string
  status: string
  statusValue: string
  availability?: 'online' | 'offline'
  flowSource: FlowSourceRow
}

const EMPTY = '—'

function payloadRecord(task: TaskRow): Record<string, unknown> {
  return task.request_payload && typeof task.request_payload === 'object'
    ? task.request_payload as Record<string, unknown>
    : {}
}

function resultRecord(task: TaskRow): Record<string, unknown> {
  return task.result_payload && typeof task.result_payload === 'object'
    ? task.result_payload as Record<string, unknown>
    : {}
}

/**
 * Resolve backup source display info purely from task payload data,
 * without any live API calls. Returns null when no historical data is available.
 */
export function resolveTaskBackupSourceResourceFromPayload(
  resource: TaskResourceRow,
  task: TaskRow,
): TaskBackupSourceResourceDisplay | null {
  const request = payloadRecord(task)
  const result = resultRecord(task)

  // 1. request_payload.cleanup_plan.source
  const cleanupPlan = request.cleanup_plan && typeof request.cleanup_plan === 'object'
    ? request.cleanup_plan as Record<string, unknown>
    : null
  const cleanupSource = cleanupPlan?.source && typeof cleanupPlan.source === 'object'
    ? cleanupPlan.source as Record<string, unknown>
    : null
  if (cleanupSource?.name) {
    const sourceName = String(cleanupSource.name)
    const sourceKind = String(cleanupSource.kind || resource.resource_subtype || '')
    const flowSource: FlowSourceRow = {
      id: `${sourceKind === 'agent' ? 'agent' : 'nas'}:${resource.resource_id}`,
      refId: resource.resource_id,
      name: sourceName,
      hostname: sourceName,
      nodeName: sourceName,
      nodeIp: '',
      status: 'removed',
      registeredAt: String(cleanupSource.registered_at || ''),
      type: sourceKind === 'agent' ? 'host' : 'nas',
    }
    return {
      backupSource: sourceName,
      endpointName: sourceName,
      endpointIp: '',
      registeredAt: String(cleanupSource.registered_at || ''),
      status: 'unregistered',
      statusValue: 'unregistered',
      availability: undefined,
      flowSource,
    }
  }

  // 2. request_payload.source_orphan_display_name
  const orphanName = typeof request.source_orphan_display_name === 'string'
    ? request.source_orphan_display_name.trim()
    : ''
  if (orphanName) {
    const sourceKind = resource.resource_subtype || 'agent'
    const flowSource: FlowSourceRow = {
      id: `${sourceKind === 'agent' ? 'agent' : 'nas'}:${resource.resource_id}`,
      refId: resource.resource_id,
      name: orphanName,
      hostname: orphanName,
      nodeName: orphanName,
      nodeIp: '',
      status: 'removed',
      registeredAt: '',
      type: sourceKind === 'agent' ? 'host' : 'nas',
    }
    return {
      backupSource: orphanName,
      endpointName: orphanName,
      endpointIp: '',
      registeredAt: '',
      status: 'unregistered',
      statusValue: 'unregistered',
      availability: undefined,
      flowSource,
    }
  }

  // 3. result_payload.sources[].source_name
  const sources = Array.isArray(result.sources) ? result.sources : []
  const matchedSource = sources.find(
    (s: unknown) => s && typeof s === 'object' && (s as Record<string, unknown>).source_name,
  ) as Record<string, unknown> | undefined
  if (matchedSource?.source_name) {
    const sourceName = String(matchedSource.source_name)
    const sourceKind = resource.resource_subtype || 'agent'
    const flowSource: FlowSourceRow = {
      id: `${sourceKind === 'agent' ? 'agent' : 'nas'}:${resource.resource_id}`,
      refId: resource.resource_id,
      name: sourceName,
      hostname: sourceName,
      nodeName: sourceName,
      nodeIp: '',
      status: 'removed',
      registeredAt: '',
      type: sourceKind === 'agent' ? 'host' : 'nas',
    }
    return {
      backupSource: sourceName,
      endpointName: sourceName,
      endpointIp: '',
      registeredAt: '',
      status: 'unregistered',
      statusValue: 'unregistered',
      availability: undefined,
      flowSource,
    }
  }

  return null
}

export async function resolveTaskBackupSourceResource(
  resource: TaskResourceRow,
  sourceType: string,
  signal?: AbortSignal,
): Promise<TaskBackupSourceResourceDisplay> {
  if (sourceType === 'agent') {
    const node = await getNode(resource.resource_id, { signal })
    const metadata = node.metadata || {}
    const hostname = String(metadata.hostname || node.name || EMPTY)
    const platformValue = String(node.os_name || metadata.os || '').toLowerCase()
    const platform = platformValue
      ? platformValue.includes('win')
        ? 'windows'
        : platformValue.includes('mac') || platformValue.includes('darwin')
          ? 'macos'
          : 'linux'
      : undefined
    const flowSource: FlowSourceRow = {
      id: `agent:${node.id}`,
      refId: node.id,
      name: node.name || EMPTY,
      hostname,
      nodeName: node.name || EMPTY,
      nodeIp: node.ip_address || '',
      status: node.status,
      availability: node.availability,
      registeredAt: node.created_at || '',
      type: 'host',
      platform,
    }
    return {
      backupSource: node.name || EMPTY,
      endpointName: hostname,
      endpointIp: node.ip_address || EMPTY,
      registeredAt: node.created_at || '',
      status: node.status || '',
      statusValue: node.status || '',
      availability: node.availability,
      flowSource,
    }
  }

  const source = await getSourceResource(resource.resource_id, { signal })
  const boundNode = source.bound_node ? await getNode(source.bound_node, { signal }) : null
  const protocolValue = String(source.config?.protocol || '').toLowerCase()
  const protocol = protocolValue === 'smb' ? 'smb' : protocolValue === 'nfs' ? 'nfs' : undefined
  const flowSource: FlowSourceRow = {
    id: `nas:${source.id}`,
    refId: source.id,
    name: source.name || EMPTY,
    hostname: source.name || EMPTY,
    nodeName: boundNode?.name || source.bound_node_name || '',
    nodeIp: boundNode?.ip_address || '',
    status: (source.status || 'inactive') as FlowSourceRow['status'],
    availability: source.availability,
    registeredAt: source.created_at || '',
    type: 'nas',
    protocol,
    boundNodeId: source.bound_node,
    mountStatus: source.mount_status,
    mountPoint: source.mount_point,
  }
  return {
    backupSource: source.name || EMPTY,
    endpointName: boundNode?.name || source.bound_node_name || EMPTY,
    endpointIp: boundNode?.ip_address || EMPTY,
    registeredAt: source.created_at || '',
    status: source.status_display || source.status || '',
    statusValue: source.status || '',
    availability: source.availability,
    flowSource,
  }
}
