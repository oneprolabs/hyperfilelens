import { api } from './api'
import { unwrapApiPayload } from './parse'
import type { ApiNode } from '../types/node'

export const CACHE_CAPABILITY = 'cache_management_v1'
export const GIB = 1024 ** 3
export type CachePolicy = { schema_version: number; revision: number; blocking_bytes: number; warning_bytes: number }
export type CacheRelocation = { task_id?: string; stage?: string; source?: string; destination?: string; error?: string }
export type CacheReport = {
  policy?: CachePolicy; application_state?: string; mode?: string; active_cache_root?: string; reported_at?: string
  relocation?: CacheRelocation; usage?: { logical_bytes: number; complete: boolean; sampled_at: string; basis: string }
  last_preflight?: { state: string; sampled_at: string; cache_root: string; available_bytes?: number | null }
  space?: { available_bytes: number | null; sampled_at: string; state: string }; space_error?: string
}
export type CacheTask = { id: string; kind: string; status: string; error?: string; result?: Record<string, unknown> }
export type NodeCacheSnapshot = { supported: boolean; policy_unit_supported?: boolean; policy: CachePolicy | null; application_state: string; report: CacheReport; last_task?: CacheTask | null }

export function nodeCacheCapability(node: Pick<ApiNode, 'metadata' | 'role'> | null | undefined): 'supported' | 'legacy' | 'unknown' {
  if (!node || !['agent', 'proxy'].includes(node.role)) return 'unknown'
  const inventory = node.metadata?.inventory
  if (!inventory || typeof inventory !== 'object') return 'unknown'
  const values = (inventory as Record<string, unknown>).capabilities
  if (Array.isArray(values) && values.includes(CACHE_CAPABILITY)) return 'supported'
  const report = (node.metadata?.cache_status || (inventory as Record<string, unknown>).cache_status) as CacheReport | undefined
  if (report?.mode === 'demand_driven') return 'supported'
  if (!Array.isArray(values)) return 'unknown'
  return 'legacy'
}
export function nodeUsesDemandCache(node: Pick<ApiNode, 'metadata' | 'role'> | null | undefined): boolean {
  if (nodeCacheCapability(node) !== 'supported') return false
  const inventory = node?.metadata?.inventory as Record<string, unknown> | undefined
  const report = (node?.metadata?.cache_status || inventory?.cache_status) as CacheReport | undefined
  return report?.mode === 'demand_driven' && report.application_state === 'applied'
}
/** GiB is binary; decimal input is truncated at the byte, never rounded in float. */
export function blockingGiBToBytes(raw: string): number | null {
  const value = raw.trim()
  if (!/^\d{1,16}(?:\.\d{1,30})?$/.test(value)) return null
  const [whole, fraction = ''] = value.split('.')
  const scale = 10n ** BigInt(fraction.length)
  const bytes = ((BigInt(whole) * scale + BigInt(fraction || '0')) * BigInt(GIB)) / scale
  if (bytes <= 0n || bytes > BigInt(Number.MAX_SAFE_INTEGER - 10 * GIB)) return null
  return Number(bytes)
}

/** Every integer byte count has an exact, terminating base-10 GiB representation. */
export function bytesToGiBInput(bytes: number): string {
  const count = BigInt(bytes)
  const unit = BigInt(GIB)
  const fraction = ((count % unit) * 10n ** 30n / unit).toString().padStart(30, '0').replace(/0+$/, '')
  return `${count / unit}${fraction ? `.${fraction}` : ''}`
}
export function cacheWarningBytes(blocking: number): number { return blocking + 10 * GIB }
export async function getNodeCache(id: number, signal?: AbortSignal): Promise<NodeCacheSnapshot> {
  return unwrapApiPayload(await api<unknown>(`/api/v1/node/nodes/${id}/cache/`, { signal }))
}
export async function operateNodeCache(id: number, body: Record<string, unknown>, signal?: AbortSignal): Promise<CacheTask> {
  return unwrapApiPayload(await api<unknown>(`/api/v1/node/nodes/${id}/cache/`, { method: 'POST', body: JSON.stringify(body), signal }))
}
export async function getCacheTask(nodeId: number, id: string, signal?: AbortSignal): Promise<CacheTask> {
  return unwrapApiPayload(await api<unknown>(`/api/v1/node/nodes/${nodeId}/cache/?task_id=${encodeURIComponent(id)}`, { signal }))
}

// Agent diagnostics stay in English in task history/logs. Map stable cache
// validation messages at the presentation boundary, including older binaries.
export function localizeCacheError(message: string, translate: (key: string) => string): string {
  const mappings = [
    ['destination overlaps protected installation, repository or log data', 'protectedPath'],
    ['critical system destination is not allowed', 'systemPath'],
    ['destination must be an absolute path', 'absolutePath'],
    ['destination aliases are not allowed', 'aliasPath'],
    ['destination parent must be an accessible existing directory', 'parentUnavailable'],
    ['destination is not a verified installation-owned cache subtree', 'unownedPath'],
    ['old and new cache roots must be distinct and not nested', 'sameOrNested'],
    ['destination available bytes ', 'insufficientSpace'],
    ['destination space check failed', 'spaceUnavailable'],
    ['cache layout migration must be completed and disposable before relocation', 'layoutNotReady'],
    ['cache policy changed; validate destination again', 'policyChanged'],
    ['destination validation has expired', 'validationExpired'],
    ['destination filesystem identity changed', 'mountChanged'],
    ['active cache mount changed or is unavailable', 'mountChanged'],
  ] as const
  const normalized = message.trim().toLowerCase()
  if (normalized.includes('the process cannot access the file because it is being used by another process')
    || normalized.includes('the process cannot access the file because another process has locked a portion')) {
    return translate('nodeCache.errors.directoryInUse')
  }
  const matched = mappings.find(([prefix]) => normalized.startsWith(prefix))
  return matched ? translate(`nodeCache.errors.${matched[1]}`) : message
}
