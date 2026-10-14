import { computed, onUnmounted, ref, watch, type Ref } from 'vue'
import { api } from '../lib/api'
import { unwrapApiPayload } from '../lib/parse'
import { currentUser, getEffectiveOrgKey } from './useAuth'

export type GovernedResourceType = 'node' | 'repository' | 'source_resource' | 'backup_config' | 'restore_plan' | 'backup_policy' | 'file_filter_rule' | 'alert_policy' | 'notification_channel'
export interface ResourceReference { resource_type: GovernedResourceType; resource_id: number | string }
export type ResourceAction = 'view' | 'manage' | 'delete' | 'upgrade' | 'run' | 'cancel' | 'test' | 'browse_data' | 'download_data' | 'delete_data' | 'use_insights' | 'run_restore' | 'cancel_restore' | 'run_backup' | 'cancel_backup' | 'use_gateway' | 'manage_insights'
interface ResourceCapability extends ResourceReference, Record<ResourceAction, boolean> {}
export type CreatableResourceType = GovernedResourceType | 'node:agent' | 'node:proxy' | 'node:gateway' | 'repository:s3' | 'repository:nas' | 'repository:proxy_fs'
interface CapabilityResponse { organization_key: string; resources: ResourceCapability[]; create: Partial<Record<CreatableResourceType, boolean>> }
const key = (kind: GovernedResourceType, id: number | string) => `${kind}:${id}`

/** Backend-authoritative UI hints. Missing/loading/failed capabilities fail closed. */
export function useResourceCapabilities(references: Ref<ResourceReference[]>) {
  const capabilities = ref(new Map<string, ResourceCapability>())
  const create = ref<CapabilityResponse['create']>({})
  const loading = ref(true)
  const failed = ref(false)
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  let generation = 0

  async function refresh() {
    const seq = ++generation
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal
    const orgKey = getEffectiveOrgKey()
    loading.value = true
    failed.value = false
    capabilities.value = new Map()
    create.value = {}
    try {
      const unique = [...new Map(references.value.map((row) => [key(row.resource_type, row.resource_id), row])).values()]
      const next = new Map<string, ResourceCapability>()
      let createActions: CapabilityResponse['create'] = {}
      for (let start = 0; start < unique.length || (start === 0 && unique.length === 0); start += 200) {
        const raw = await api<unknown>('/api/v1/iam/resource-capabilities/', {
          method: 'POST', signal,
          body: JSON.stringify({ resources: unique.slice(start, start + 200) }),
        })
        const result = unwrapApiPayload<CapabilityResponse>(raw)
        if (result.organization_key !== orgKey || !Array.isArray(result.resources)) throw new Error('Invalid capability response')
        result.resources.forEach((row) => next.set(key(row.resource_type, row.resource_id), row))
        createActions = { ...createActions, ...(result.create || {}) }
      }
      if (!signal.aborted && !disposed && generation === seq && getEffectiveOrgKey() === orgKey) {
        capabilities.value = next
        create.value = createActions
      }
    } catch {
      if (!signal.aborted && !disposed && generation === seq) failed.value = true
    } finally {
      if (!signal.aborted && !disposed && generation === seq) loading.value = false
    }
  }
  function permitted(kind: GovernedResourceType, id: number | string | null | undefined, action: ResourceAction = 'manage') {
    return !loading.value && !failed.value && id != null && capabilities.value.get(key(kind, id))?.[action] === true
  }
  const canCreate = (kind: CreatableResourceType) => !loading.value && !failed.value && create.value[kind] === true
  watch(() => [references.value.map((row) => key(row.resource_type, row.resource_id)).sort().join('|'), currentUser.value?.id, getEffectiveOrgKey()], () => {
    ++generation
    controller?.abort()
    capabilities.value = new Map()
    create.value = {}
    loading.value = true
    clearTimeout(timer)
    timer = setTimeout(() => { void refresh() }, 0)
  }, { immediate: true })
  // Refresh advisory controls after tab focus; the API always rechecks writes.
  const onFocus = () => { if (!disposed) void refresh() }
  window.addEventListener('focus', onFocus)
  onUnmounted(() => {
    disposed = true
    clearTimeout(timer)
    controller?.abort()
    window.removeEventListener('focus', onFocus)
  })
  return { permitted, canCreate, refresh, loading: computed(() => loading.value), failed: computed(() => failed.value) }
}
