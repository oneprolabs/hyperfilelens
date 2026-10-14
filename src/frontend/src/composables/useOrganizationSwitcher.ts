import { computed, onMounted, readonly, ref, watch } from 'vue'
import { api } from '../lib/api'
import { asList, unwrapApiPayload } from '../lib/parse'
import {
  currentUser,
  fetchCurrentUser,
  getEffectiveOrgKey,
  setStoredOrgKey,
} from './useAuth'

export type OrganizationOption = {
  id: number
  key: string
  name: string
}

const organizations = ref<OrganizationOption[]>([])
const loading = ref(false)
const canCreate = ref(false)

let loadedUserId: number | null | undefined
let inflight: { userId: number | null; promise: Promise<void> } | null = null

async function loadOrganizations(force = false): Promise<void> {
  const userId = currentUser.value?.id ?? null
  if (!force && loadedUserId === userId) return

  if (inflight?.userId === userId) return inflight.promise

  if (inflight) {
    await inflight.promise
    return loadOrganizations(force)
  }

  loading.value = true
  const promise = (async () => {
    try {
      const [directory, policy] = await Promise.allSettled([
        api<unknown>('/api/v1/iam/my-organizations/'),
        api<unknown>('/api/v1/iam/organization-creation-options/'),
      ])
      if ((currentUser.value?.id ?? null) !== userId) return
      organizations.value = directory.status === 'fulfilled' ? asList<OrganizationOption>(directory.value) : []
      canCreate.value = policy.status === 'fulfilled'
        && unwrapApiPayload<{ can_create: boolean }>(policy.value)?.can_create === true
    } catch {
      if ((currentUser.value?.id ?? null) === userId) { organizations.value = []; canCreate.value = false }
    } finally {
      if ((currentUser.value?.id ?? null) === userId) loadedUserId = userId
    }
  })()
  inflight = { userId, promise }

  try {
    await promise
  } finally {
    if (inflight?.promise === promise) {
      inflight = null
      loading.value = false
    }
  }
}

async function switchOrganization(orgKey: string) {
  if (!orgKey || orgKey === getEffectiveOrgKey()) return
  setStoredOrgKey(orgKey)
  await fetchCurrentUser()
  window.location.reload()
}

export function useOrganizationSwitcher() {
  const currentKey = computed(() => getEffectiveOrgKey())
  const showSwitcher = computed(() => organizations.value.length > 1 || canCreate.value)

  onMounted(() => {
    void loadOrganizations()
  })

  watch(
    () => currentUser.value?.id,
    () => {
      void loadOrganizations(true)
    },
  )

  return {
    organizations: readonly(organizations),
    loading: readonly(loading),
    currentKey,
    showSwitcher,
    loadOrganizations,
    switchOrganization,
    canCreate: readonly(canCreate),
  }
}
