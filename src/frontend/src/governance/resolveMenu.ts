import { Building2, Layers3, ShieldUser, Users } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { governanceMenuItems } from '@ext/platform/governance/menu'
import { currentUser } from '../composables/useAuth'

type GovernanceMenuItem = {
  id: string
  labelKey: string
  to: string
  icon: 'organization' | 'members' | 'roles' | 'resources'
  requiredRoles?: readonly string[]
}

const icons = { organization: Building2, members: Users, roles: ShieldUser, resources: Layers3 } as const

export function isGovernanceMenuItemVisible(item: GovernanceMenuItem, role: string): boolean {
  return !item.requiredRoles || item.requiredRoles.includes(role.trim())
}

/** Convert the optional EE menu contribution into Host menu items. */
export function resolveGovernanceMenu(t: (key: string) => string): MenuItem[] {
  const role = currentUser.value?.access_profile?.role?.trim() || ''
  return (governanceMenuItems as GovernanceMenuItem[])
    .filter((item) => isGovernanceMenuItemVisible(item, role))
    .map((item) => ({
      label: t(item.labelKey),
      to: item.to,
      icon: icons[item.icon],
      pageTitle: t(item.labelKey),
    }))
}
