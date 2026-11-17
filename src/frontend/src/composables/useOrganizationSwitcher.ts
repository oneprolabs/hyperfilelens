import { computed, onMounted, readonly, ref, watch } from 'vue'
import { api } from '../lib/api'
import { asList, unwrapApiPayload } from '../lib/parse'
import { currentUser, getEffectiveOrgKey } from './useAuth'
import { completeOrganizationSwitch } from '../lib/organizationSwitch'

export type OrganizationOption = { id: number; key: string; name: string }
const organizations = ref<OrganizationOption[]>([])
const loading = ref(false)
const switching = ref(false)
const loadFailed = ref(false)
let loadedUserId: number | null | undefined
let inflight: { userId: number; promise: Promise<void> } | null = null

async function loadOrganizations(force = false): Promise<void> {
  const userId = currentUser.value?.id
  if (!userId) {
    organizations.value = []
    loadedUserId = null
    return
  }
  if (!force && loadedUserId === userId) return
  if (inflight?.userId === userId) return inflight.promise
  if (inflight) {
    await inflight.promise
    return loadOrganizations(force)
  }
  organizations.value = []
  loading.value = true
  loadFailed.value = false
  const promise = (async () => {
    try {
      const data = await api<unknown>('/api/v1/iam/my-organizations/')
      if (currentUser.value?.id !== userId) return
      organizations.value = asList<OrganizationOption>(data)
      loadedUserId = userId
    } catch {
      if (currentUser.value?.id === userId) loadFailed.value = true
    }
  })()
  inflight = { userId, promise }
  try {
    await promise
  } finally {
    if (inflight?.promise === promise) { inflight = null; loading.value = false }
  }
}

async function switchOrganization(orgKey: string): Promise<boolean> {
  const previousKey = getEffectiveOrgKey()
  if (!orgKey || orgKey === previousKey || switching.value) return false
  const userId = currentUser.value?.id
  switching.value = true
  try {
    const response = unwrapApiPayload<{ access_profile?: { org_key: string; landing_path: string } }>(
      await api('/api/v1/iam/switch-organization/', {
        method: 'POST', body: JSON.stringify({ org_key: orgKey }),
      }),
    )
    const profile = response?.access_profile
    if (currentUser.value?.id !== userId || getEffectiveOrgKey() !== previousKey) return false
    if (profile?.org_key !== orgKey || !profile.landing_path?.startsWith('/')
      || profile.landing_path.startsWith('//') || profile.landing_path.startsWith('/platform-ops')) {
      throw new Error('Invalid organization switch response.')
    }
    // Do not change the live access profile in the old page. A full navigation
    // tears down old lists, sockets and requests before the new tenant mounts.
    completeOrganizationSwitch(orgKey, profile.landing_path)
    return true
  } finally {
    switching.value = false
  }
}

export function useOrganizationSwitcher() {
  const currentKey = computed(() => currentUser.value?.access_profile?.org_key || getEffectiveOrgKey())
  const currentName = computed(() => organizations.value.find(org => org.key === currentKey.value)?.name
    || currentUser.value?.access_profile?.org_name || '')
  onMounted(() => { void loadOrganizations() })
  watch(() => currentUser.value?.id, () => { void loadOrganizations(true) })
  return {
    organizations: readonly(organizations), loading: readonly(loading), switching: readonly(switching),
    loadFailed: readonly(loadFailed), currentKey, currentName,
    showSwitcher: computed(() => organizations.value.length > 1),
    loadOrganizations, switchOrganization,
  }
}
