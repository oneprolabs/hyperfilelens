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

/**
 * Advisory UI hints; the backend rechecks authorization when an action executes.
 * Unknown/failed rows fail closed; same-scope background checks retain confirmed hints.
 */
export function useResourceCapabilities(references: Ref<ResourceReference[]>) {
  const capabilities = ref(new Map<string, ResourceCapability>())
  const create = ref<CapabilityResponse['create']>({})
  const loading = ref(true)
  const failed = ref(false)
  let controller: AbortController | null = null
  let disposed = false
  let generation = 0
  let scopeKey = ''
  let referenceKey = ''
  let createResolved = false
  let pending: { scope: string; references: string; queriedKeys: Set<string>; promise: Promise<void> } | null = null

  const uniqueReferences = computed(() => [...new Map(
    references.value.map((row) => [key(row.resource_type, row.resource_id), row]),
  ).values()].sort((a, b) => key(a.resource_type, a.resource_id).localeCompare(key(b.resource_type, b.resource_id))))
  const requestedKeys = computed(() => new Set(
    uniqueReferences.value.map((row) => key(row.resource_type, row.resource_id)),
  ))
  const getReferenceKey = () => JSON.stringify([...requestedKeys.value])
  const normalizedPermissions = (values: string[] | undefined) => [...new Set(values || [])].sort()
  // Session probes replace the User object. Compare authorization values, not
  // object identity, while still invalidating known Role/permission changes.
  const userAuthorizationKey = computed(() => {
    const user = currentUser.value
    const profile = user?.access_profile
    return JSON.stringify([
      user?.id ?? null, profile?.org_key ?? '',
      user?.is_staff ?? false, user?.requires_password_change ?? false,
      profile?.role ?? '',
      normalizedPermissions(profile?.permissions),
      normalizedPermissions(profile?.group_permissions),
    ])
  })
  function getScopeKey() {
    // Read storage directly too: org selection can change before User updates.
    return JSON.stringify([getEffectiveOrgKey(), userAuthorizationKey.value])
  }

  function cancelPending() {
    ++generation
    controller?.abort()
    pending = null
    loading.value = false
  }

  function synchronizeScope() {
    const nextScope = getScopeKey()
    const nextReferences = getReferenceKey()
    if (scopeKey !== nextScope || referenceKey !== nextReferences) {
      const interruptedCheck = scopeKey === nextScope && pending !== null
      cancelPending()
      if (scopeKey !== nextScope) {
        capabilities.value = new Map()
        create.value = {}
        createResolved = false
        failed.value = false
      } else {
        capabilities.value = new Map(
          [...capabilities.value].filter(([id]) => requestedKeys.value.has(id)),
        )
      }
      scopeKey = nextScope
      referenceKey = nextReferences
      return interruptedCheck
    }
    return false
  }

  function load(resources: ResourceReference[]): Promise<void> {
    if (disposed || !currentUser.value || !getEffectiveOrgKey()) return Promise.resolve()
    const queriedKeys = new Set(resources.map((row) => key(row.resource_type, row.resource_id)))
    // Incremental requests only cover their missing rows, not the entire page.
    const running = pending
    if (running?.scope === scopeKey && running.references === referenceKey
      && [...queriedKeys].every((id) => running.queriedKeys.has(id))) return running.promise
    const seq = ++generation
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal
    const orgKey = getEffectiveOrgKey()
    const requestScope = scopeKey
    const requestReferences = referenceKey
    const isCurrent = () => !signal.aborted && !disposed && generation === seq
      && getScopeKey() === requestScope && getReferenceKey() === requestReferences
      && getEffectiveOrgKey() === orgKey
    loading.value = true
    failed.value = false
    const promise = Promise.resolve().then(async () => {
      try {
        const next = new Map<string, ResourceCapability>()
        let createActions: CapabilityResponse['create'] = {}
        for (let start = 0; start < resources.length || start === 0; start += 200) {
          if (!isCurrent()) return
          const raw = await api<unknown>('/api/v1/iam/resource-capabilities/', {
            method: 'POST', signal,
            body: JSON.stringify({ resources: resources.slice(start, start + 200) }),
          })
          if (!isCurrent()) return
          const result = unwrapApiPayload<CapabilityResponse>(raw)
          if (result?.organization_key !== orgKey || !Array.isArray(result.resources)) throw new Error('Invalid capability response')
          result.resources.forEach((row) => {
            const id = key(row.resource_type, row.resource_id)
            if (queriedKeys.has(id)) next.set(id, row)
          })
          createActions = { ...createActions, ...(result.create || {}) }
        }
        if (!isCurrent()) return
        // Keep unchanged resources during incremental loads. Omitted or denied
        // rows replace old grants rather than accidentally retaining access.
        capabilities.value = new Map([
          ...[...capabilities.value].filter(([id]) => !queriedKeys.has(id)),
          ...next,
        ])
        create.value = createActions
        createResolved = true
      } catch {
        if (isCurrent()) {
          capabilities.value = new Map(
            [...capabilities.value].filter(([id]) => !queriedKeys.has(id)),
          )
          create.value = {}
          createResolved = false
          failed.value = true
        }
      } finally {
        if (!disposed && generation === seq) {
          loading.value = false
          pending = null
        }
      }
    })
    pending = { scope: requestScope, references: requestReferences, queriedKeys, promise }
    return promise
  }
  function refresh(options: { invalidate?: boolean } = {}): Promise<void> {
    synchronizeScope()
    // A rejected action is authoritative, unlike an ordinary background check.
    if (options.invalidate) {
      cancelPending()
      capabilities.value = new Map()
      create.value = {}
      createResolved = false
    }
    return load(uniqueReferences.value)
  }
  function permitted(kind: GovernedResourceType, id: number | string | null | undefined, action: ResourceAction = 'manage') {
    return !disposed && scopeKey === getScopeKey() && id != null
      && requestedKeys.value.has(key(kind, id))
      && capabilities.value.get(key(kind, id))?.[action] === true
  }
  const canCreate = (kind: CreatableResourceType) => !disposed && scopeKey === getScopeKey() && create.value[kind] === true
  watch([getScopeKey, getReferenceKey], () => {
    const interruptedCheck = synchronizeScope()
    // Do not discard an already requested recheck just because its old list
    // disappeared. Preserve hints while rechecking the new scope (even empty).
    const resources = uniqueReferences.value.filter((row) =>
      interruptedCheck || !capabilities.value.has(key(row.resource_type, row.resource_id)),
    )
    if (interruptedCheck || resources.length || !createResolved) void load(resources)
  }, { immediate: true })
  // A successful list reload can return the same ids after a transient failure.
  // Retry only on new list data, never on the failed flag itself (no retry loop).
  watch(references, () => {
    if (failed.value) void refresh()
  })
  // Refresh advisory controls after tab focus; the API always rechecks writes.
  const onFocus = () => { if (!disposed) void refresh() }
  window.addEventListener('focus', onFocus)
  onUnmounted(() => {
    disposed = true
    cancelPending()
    window.removeEventListener('focus', onFocus)
  })
  return { permitted, canCreate, refresh, loading: computed(() => loading.value), failed: computed(() => failed.value) }
}
