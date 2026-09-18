import { Building2, Layers3, ShieldUser, Users } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { governanceMenuItems } from '@ext/platform/governance/menu'

type GovernanceMenuItem = {
  id: string
  labelKey: string
  to: string
  icon: 'organization' | 'members' | 'roles' | 'resources'
}

const icons = { organization: Building2, members: Users, roles: ShieldUser, resources: Layers3 } as const

/** Convert the optional EE menu contribution into Host menu items. */
export function resolveGovernanceMenu(t: (key: string) => string): MenuItem[] {
  return (governanceMenuItems as GovernanceMenuItem[]).map((item) => ({
    label: t(item.labelKey),
    to: item.to,
    icon: icons[item.icon],
    pageTitle: t(item.labelKey),
  }))
}
