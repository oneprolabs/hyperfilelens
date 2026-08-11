import { formatNodeBytes } from './nodeInventoryDisplay'
import type { FlowSourceRow } from '../pages/protection/composables/useFlowSourceAggregate'

export type FlowSourceConnectionRow = Pick<
  FlowSourceRow,
  'type' | 'hostname' | 'nodeName' | 'nodeIp' | 'name'
>

export type FlowReadyStatus = {
  label: string
  tag: 'success' | 'danger' | 'warning' | 'info' | 'neutral'
}

export type FlowSourceDisplayLabels = {
  dash: string
  host: string
  nas: string
  protocolSmb: string
  protocolNfs: string
  online: string
  offline: string
  registered: string
  localAgent: string
}

export function flowSourceNodeParts(row: Pick<FlowSourceRow, 'nodeName' | 'nodeIp'>) {
  const name = (row.nodeName || '').trim()
  const ip = (row.nodeIp || '').trim()
  return { name: name || '—', ip }
}

export function flowSourceKindLabel(row: Pick<FlowSourceRow, 'type'>, labels: Pick<FlowSourceDisplayLabels, 'host' | 'nas'>) {
  return row.type === 'host' ? labels.host : labels.nas
}

export function flowSourceProtocolLabel(
  row: Pick<FlowSourceRow, 'type' | 'protocol'>,
  labels: Pick<FlowSourceDisplayLabels, 'protocolSmb' | 'protocolNfs'>,
) {
  if (row.type !== 'nas') return ''
  return row.protocol === 'smb' ? labels.protocolSmb : labels.protocolNfs
}

export function flowSourceConnectionPrimary(row: FlowSourceConnectionRow) {
  if (row.type === 'nas') {
    return (row.nodeName || '').trim() || '—'
  }
  return (row.hostname || '').trim() || (row.nodeName || '').trim() || (row.name || '').trim() || '—'
}

export function flowSourceConnectionSecondary(row: FlowSourceConnectionRow) {
  return (row.nodeIp || '').trim() || '—'
}

export function flowSourceHostConnectionTitle(row: FlowSourceRow) {
  const ip = (row.nodeIp || '').trim()
  const hostname = (row.hostname || row.name || '').trim()
  if (hostname && ip) return `${hostname} · ${ip}`
  return hostname || ip || '—'
}

/** NAS secondary line: proxy IP + mount path under agent data dir. */
export function flowSourceNasAccessLine(row: FlowSourceRow) {
  const nodeIp = (row.nodeIp || '').trim()
  const mountPoint = (row.mountPoint || '').trim()
  if (nodeIp && mountPoint) {
    const normalizedMount = mountPoint.startsWith('/') ? mountPoint : `/${mountPoint}`
    return `${nodeIp}:${normalizedMount}`
  }
  if (mountPoint) return mountPoint
  if (nodeIp) return nodeIp
  return ''
}

export function flowSourceNasAccessTitle(row: FlowSourceRow) {
  const access = flowSourceNasAccessLine(row)
  const nodeName = (row.nodeName || '').trim()
  if (!access) return nodeName
  if (!nodeName) return access
  return `${nodeName} · ${access}`
}

export function flowSourceReadyStatus(
  row: FlowSourceRow,
  labels: Pick<FlowSourceDisplayLabels, 'registered'>,
): FlowReadyStatus {
  const label = row.status
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
  return {
    label: row.status === 'active' ? labels.registered : label,
    tag: row.status === 'active'
      ? 'success'
      : row.status === 'failed' || row.status === 'upgrade_failed' || row.status === 'deregistration_failed' || row.status === 'error'
        ? 'danger'
        : 'neutral',
  }
}

export function flowSourceCpuCores(row: Pick<FlowSourceRow, 'cpuCores'>) {
  const cores = row.cpuCores
  return typeof cores === 'number' && Number.isFinite(cores) && cores > 0 ? cores : null
}

export function flowSourceMemoryText(row: Pick<FlowSourceRow, 'memoryTotalBytes'>) {
  const total = row.memoryTotalBytes
  return typeof total === 'number' && Number.isFinite(total) && total > 0 ? formatNodeBytes(total) : '—'
}

export function flowSourceDiskCountText(row: Pick<FlowSourceRow, 'diskCount'>) {
  const count = row.diskCount
  return typeof count === 'number' && Number.isFinite(count) && count > 0 ? String(count) : '—'
}
