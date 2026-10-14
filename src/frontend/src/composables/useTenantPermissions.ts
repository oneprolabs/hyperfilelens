import { computed } from 'vue'
import { currentUser } from './useAuth'

/** Coarse navigation hints only; resource buttons use scoped capabilities. */
export function useTenantPermissions() {
  const profile = computed(() => currentUser.value?.access_profile)
  const organization = computed(() => new Set(profile.value?.permissions || []))
  const group = computed(() => new Set(profile.value?.group_permissions || []))
  const has = (key: string, organizationOnly = false) => !__HFL_EXTENSIONS_FRONTEND__ || organization.value.has(key)
    || (!organizationOnly && group.value.has(key))
  return { has }
}
