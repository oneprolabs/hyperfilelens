import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Gauge } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { resolveGovernanceMenu } from '../governance/resolveMenu'
import { useTenantPermissions } from './useTenantPermissions'
import { canViewConfigurationPage } from '../lib/configurationAccess'
import { resolveGovernanceRoutes } from '../governance/resolveRoutes'
import { useAccountCenterMenus } from './useAccountCenterMenus'

/** Configuration sidebar: authorized organization pages and personal account pages. */
export function useNodeSideNav() {
  const { t } = useI18n()
  const { has } = useTenantPermissions()
  const accountMenus = useAccountCenterMenus()
  return computed<MenuItem[]>(() => {
    const routes = resolveGovernanceRoutes()
    const children = [
      ...resolveGovernanceMenu(t),
      { label: t('settings.nav.subscription'), to: '/node/subscription', icon: Gauge },
    ].filter(item => canViewConfigurationPage(item.to || '', routes, has))
    return [
      ...(children.length ? [{
        label: t('assetsPage.side.groupGovernance'),
        children,
      }] : []),
      ...accountMenus.value,
    ]
  })
}
