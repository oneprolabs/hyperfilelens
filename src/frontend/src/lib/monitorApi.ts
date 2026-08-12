import { api } from './api'
import { unwrapApiPayload } from './parse'
import type { Availability, NodeRole } from '../types/node'

export type SystemMetricSample = {
  timestamp: string
  cpu?: { usagePercent?: number; usage_percent?: number; logicalCores?: number; logical_cores?: number }
  memory?: { percent?: number; used?: number; total?: number; available?: number }
  swap?: { percent?: number; used?: number; total?: number }
  disks?: Array<{
    device?: string
    mountpoint?: string
    percent?: number
    used?: number
    total?: number
  }>
  diskIo?: Array<Record<string, unknown>>
  disk_io?: Array<Record<string, unknown>>
  networks?: Array<Record<string, unknown>>
  loadAverage?: number[]
  load_average?: number[]
  boot_time?: number
  bootTime?: number
}

export type SystemMonitorPayload = {
  host: {
    hostname?: string
    platform?: string
    python?: string
    bootTime?: number
    boot_time?: number
    role?: string
    nodeId?: number
    node_id?: number
    nodeName?: string
    node_name?: string
  }
  range: { startAt?: string; start_at?: string; endAt?: string; end_at?: string; count?: number }
  current: SystemMetricSample
  series: SystemMetricSample[]
  resourceType?: string
  resource_type?: string
  resourceId?: string
  resource_id?: string
}

export type MonitorNodeItem = {
  id: number
  name: string
  role: NodeRole
  status: string
  availability: Availability
  hostname: string
  platform: string
  resourceType?: string
  resource_type?: string
  lastSeenAt?: string | null
  last_seen_at?: string | null
}

export type MonitorNodeListPayload = {
  items: MonitorNodeItem[]
}

export type NodeMonitorRole = 'agent' | 'proxy' | 'gateway'

const ROLE_BY_SOURCE: Record<NodeMonitorRole, NodeRole> = {
  agent: 'agent',
  proxy: 'proxy',
  gateway: 'gateway',
}

export function nodeRoleForSource(sourceType: NodeMonitorRole): NodeRole {
  return ROLE_BY_SOURCE[sourceType]
}

export async function fetchMonitorNodes(role: NodeMonitorRole): Promise<MonitorNodeItem[]> {
  const qs = new URLSearchParams({ role: nodeRoleForSource(role) }).toString()
  const raw = await api<unknown>(`/api/v1/monitors/nodes/?${qs}`)
  const payload = unwrapApiPayload<MonitorNodeListPayload>(raw)
  return payload.items || []
}

export async function fetchNodeMonitor(
  nodeId: number,
  params?: { hours?: number; startAt?: string; endAt?: string },
): Promise<SystemMonitorPayload> {
  const query: Record<string, string> = {}
  if (params?.hours !== undefined) query.hours = String(params.hours)
  if (params?.startAt) query.start_at = params.startAt
  if (params?.endAt) query.end_at = params.endAt
  const qs = new URLSearchParams(query).toString()
  const path = qs
    ? `/api/v1/monitors/nodes/${nodeId}/?${qs}`
    : `/api/v1/monitors/nodes/${nodeId}/`
  const raw = await api<unknown>(path)
  return unwrapApiPayload<SystemMonitorPayload>(raw)
}

export async function fetchSystemMonitor(params?: {
  hours?: number
  startAt?: string
  endAt?: string
}): Promise<SystemMonitorPayload> {
  const query: Record<string, string> = {}
  if (params?.hours !== undefined) query.hours = String(params.hours)
  if (params?.startAt) query.start_at = params.startAt
  if (params?.endAt) query.end_at = params.endAt
  const qs = new URLSearchParams(query).toString()
  const path = qs ? `/api/v1/monitors/system/?${qs}` : '/api/v1/monitors/system/'
  const raw = await api<unknown>(path)
  return unwrapApiPayload<SystemMonitorPayload>(raw)
}

export function formatNodeEntityLabel(node: MonitorNodeItem): string {
  const host = node.hostname || node.name || String(node.id)
  const platform = node.platform || ''
  return platform ? `${host} (${platform})` : host
}
