import type { EnrollmentOs } from './nodeApi'
import type { ApiNode, NodeStoragePool, NodeStoragePoolRow } from '../types/node'
import { installPathsSummary } from './nodeInstallCommands'
import { formatAppDateTime } from './dateTime'

export const DETAIL_EMPTY = '—'

export function isDetailEmpty(value: string): boolean {
  return value.trim() === DETAIL_EMPTY
}

export function nodeInventory(node: ApiNode): Record<string, unknown> {
  const meta = node.metadata
  if (!meta || typeof meta !== 'object') return {}
  const inv = meta.inventory
  if (inv && typeof inv === 'object') return inv as Record<string, unknown>
  return meta as Record<string, unknown>
}

export function nodeHostname(node: ApiNode): string {
  const inv = nodeInventory(node)
  const hostname = String(inv.hostname || '').trim()
  return hostname || node.name
}

export function nodeEnrollmentOs(node: ApiNode): EnrollmentOs {
  const inv = nodeInventory(node)
  const raw = String(inv.os || inv.platform || node.os_name || '').trim().toLowerCase()
  if (raw.includes('darwin') || raw.includes('mac')) return 'macos'
  if (raw.includes('windows') || raw === 'win32' || raw === 'win64' || raw.startsWith('win ')) return 'windows'
  return 'linux'
}

export type NodeOsPlatformLabels = {
  linux: string
  windows: string
  macos: string
}

export function nodePlatformLabel(os: EnrollmentOs, labels: NodeOsPlatformLabels): string {
  if (os === 'windows') return labels.windows
  if (os === 'macos') return labels.macos
  return labels.linux
}

export function nodeOsVersionText(node: ApiNode, platformLabel: string): string {
  const name = String(node.os_name || '').trim()
  if (!name) return ''
  if (name.toLowerCase() === platformLabel.toLowerCase()) return ''
  return name
}

export function nodeArch(node: ApiNode): string {
  const inv = nodeInventory(node)
  const arch = String(inv.arch || '').trim()
  if (!arch) return ''
  if (arch === 'amd64') return 'x86_64'
  return arch
}

/** Full OS detail for tooltip (version string + architecture). */
export function nodeOsDetailText(node: ApiNode): string {
  const parts: string[] = []
  const osName = String(node.os_name || '').trim()
  if (osName) parts.push(osName)
  const arch = nodeArch(node)
  if (arch) parts.push(arch)
  return parts.join(' · ')
}

export function nodeDiskUsageParts(node: ApiNode): { used: number; total: number } {
  const inv = nodeInventory(node)
  if (
    Array.isArray(inv.capabilities)
    && inv.capabilities.includes('storage_inventory_v1')
    && (
      inv.storage_inventory_status !== 'ready'
      || !Array.isArray(inv.local_storage_pools)
    )
  ) {
    return { used: 0, total: 0 }
  }
  return {
    used: Number(inv.disk_used_bytes || 0),
    total: Number(inv.disk_total_bytes || 0),
  }
}

export function nodeStoragePoolRows(
  node: ApiNode,
  field: 'local_storage_pools' | 'network_storage_pools',
): NodeStoragePoolRow[] {
  const inventory = nodeInventory(node)
  const capabilities = Array.isArray(inventory.capabilities) ? inventory.capabilities : []
  if (!capabilities.includes('storage_inventory_v1')) return []
  const statusField = field === 'local_storage_pools'
    ? 'storage_inventory_status'
    : 'network_storage_inventory_status'
  if (inventory[statusField] !== 'ready') return []
  const value = inventory[field]
  if (!Array.isArray(value)) return []
  const bytes = (raw: unknown): number => {
    const parsed = Number(raw)
    return Number.isFinite(parsed) ? Math.max(0, parsed) : 0
  }
  return value
    .filter((item): item is NodeStoragePool => Boolean(item && typeof item === 'object'))
    .map((item, index) => ({
      key: String(item.key || `storage-pool-${index}`),
      device: String(item.device || '').trim(),
      fsType: String(item.fs_type || '').trim(),
      mountPoints: Array.isArray(item.mount_points)
        ? [...new Set(item.mount_points.map((point) => String(point || '').trim()).filter(Boolean))]
        : [],
      totalBytes: bytes(item.total_bytes),
      usedBytes: bytes(item.used_bytes),
      availableBytes: bytes(item.free_bytes),
    }))
    .filter((item) => item.totalBytes > 0)
}

export function nodeSupportsStorageInventory(node: ApiNode): boolean {
  const capabilities = nodeInventory(node).capabilities
  return Array.isArray(capabilities) && capabilities.includes('storage_inventory_v1')
}

export function nodeHasStorageInventorySnapshot(node: ApiNode): boolean {
  const inventory = nodeInventory(node)
  return nodeSupportsStorageInventory(node)
    && inventory.storage_inventory_status === 'ready'
    && Array.isArray(inventory.local_storage_pools)
}

export function nodeHasNetworkStorageInventorySnapshot(node: ApiNode): boolean {
  const inventory = nodeInventory(node)
  return nodeSupportsStorageInventory(node)
    && inventory.network_storage_inventory_status === 'ready'
    && Array.isArray(inventory.network_storage_pools)
}

export function nodeDiskUsagePercent(node: ApiNode): number {
  const { used, total } = nodeDiskUsageParts(node)
  if (!total || total <= 0) return 0
  return Math.min(100, Math.round((used / total) * 100))
}

export function nodeCpuCores(node: ApiNode): number | null {
  const inv = nodeInventory(node)
  const cores = Number(inv.cpu_cores ?? inv.cpu_logical_cores ?? inv.logical_cores)
  if (Number.isFinite(cores) && cores > 0) return cores
  return null
}

export function nodeMemoryTotalBytes(node: ApiNode): number | null {
  const inv = nodeInventory(node)
  const total = Number(inv.memory_total_bytes)
  if (Number.isFinite(total) && total > 0) return total
  return null
}

export function nodeDiskCount(node: ApiNode): number | null {
  const inv = nodeInventory(node)
  if (
    Array.isArray(inv.capabilities)
    && inv.capabilities.includes('storage_inventory_v1')
    && (
      inv.storage_inventory_status !== 'ready'
      || !Array.isArray(inv.local_storage_pools)
    )
  ) {
    return null
  }
  const count = Number(inv.disk_count)
  if (Number.isFinite(count) && count > 0) return count
  return null
}

export function nodeMacAddress(node: ApiNode): string {
  const inv = nodeInventory(node)
  return String(inv.mac_address || inv.mac || '').trim()
}

export type NodeInstallDirs = {
  installDir: string
  dataDir: string
}

export function nodeInstallDirs(node: ApiNode): NodeInstallDirs {
  const inv = nodeInventory(node)
  const os = nodeEnrollmentOs(node)
  const defaults = installPathsSummary(os)
  const installDir = String(inv.install_path || '').trim() || defaults.installDir
  const dataDir = String(inv.root_path || inv.data_path || '').trim() || defaults.dataDir
  return { installDir, dataDir }
}

/** @deprecated Use nodeInstallDirs().installDir */
export function nodeInstallPath(node: ApiNode): string {
  return nodeInstallDirs(node).installDir
}

export function nodeExternalId(node: ApiNode): string {
  return nodeExternalIdForRole(node.id, node.role)
}

export function nodeExternalIdForRole(id: number, role: ApiNode['role'] = 'proxy'): string {
  const prefix = role === 'proxy' ? 'PRX' : 'AGT'
  const idPart = String(Math.abs(id)).padStart(5, '0')
  const suffix = String.fromCharCode(65 + (Math.abs(id) % 26))
  return `${prefix}-${idPart}-${suffix}`
}

/** Two-line proxy cell: primary = name (external id). */
export function proxyNodeStackPrimaryLine(
  name: string | null | undefined,
  node: Pick<ApiNode, 'id' | 'role' | 'name'> | null,
  boundNodeId?: number | null,
): string {
  const trimmed = (name || node?.name || '').trim()
  if (!trimmed) return ''
  if (node) return `${trimmed} (${nodeExternalId(node as ApiNode)})`
  if (boundNodeId != null && Number.isFinite(boundNodeId) && boundNodeId > 0) {
    return `${trimmed} (${nodeExternalIdForRole(boundNodeId, 'proxy')})`
  }
  return trimmed
}

/** Two-line proxy cell: secondary = IP. */
export function proxyNodeStackIpLine(
  node: Pick<ApiNode, 'ip_address'> | null,
  fallbackIp?: string | null,
): string {
  return (node?.ip_address?.trim() || fallbackIp?.trim() || '')
}

/** Proxy select label: name (ip) (external id). */
export function proxyNodeSelectLine(node: Pick<ApiNode, 'id' | 'role' | 'name' | 'ip_address'>): string {
  const name = (node.name || '').trim() || '—'
  const ip = (node.ip_address || '').trim() || '—'
  const externalId = nodeExternalId(node as ApiNode)
  return `${name} (${ip}) (${externalId})`
}

export function nodeBootTimeEpoch(node: ApiNode): number | null {
  const inv = nodeInventory(node)
  const boot = inv.boot_time ?? inv.bootTime
  if (typeof boot === 'number' && Number.isFinite(boot) && boot > 0) {
    return boot > 1e12 ? Math.floor(boot / 1000) : Math.floor(boot)
  }
  if (typeof boot === 'string' && boot.trim()) {
    const parsed = Date.parse(boot)
    if (!Number.isNaN(parsed)) return Math.floor(parsed / 1000)
  }
  return null
}

export function nodeUptimeSeconds(node: ApiNode, online: boolean): number | null {
  if (!online) return null
  const boot = nodeBootTimeEpoch(node)
  if (boot != null) return Math.max(0, Math.floor(Date.now() / 1000 - boot))
  const created = node.created_at ? Date.parse(node.created_at) : NaN
  if (!Number.isNaN(created)) return Math.max(0, Math.floor((Date.now() - created) / 1000))
  return null
}

export function formatNodeOsName(node: ApiNode): string {
  const name = String(node.os_name || '').trim()
  if (name) return name
  return '—'
}

export function formatNodeBytes(n?: number): string {
  const v = Number(n || 0)
  if (!v) return DETAIL_EMPTY
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let u = 0
  let x = v
  while (x >= 1024 && u < units.length - 1) {
    x /= 1024
    u += 1
  }
  return `${x.toFixed(1)} ${units[u]}`
}

export function formatNodeDate(iso?: string | null): string {
  return formatAppDateTime(iso, '—')
}
