import { computed } from 'vue'
import { currentUser } from './useAuth'
import { insightPermissionHints } from '../lib/insightAccess'

/** Coarse navigation hints only; resource buttons use scoped capabilities. */
export function useTenantPermissions() {
  const profile = computed(() => currentUser.value?.access_profile)
  const has: ReturnType<typeof insightPermissionHints> = (key, organizationOnly = false, capability) =>
    insightPermissionHints(profile.value)(key, organizationOnly, capability)
  return { has, profile }
}
