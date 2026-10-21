import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { CreditCard } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { resolveGovernanceMenu } from '../governance/resolveMenu'
import { useTenantPermissions } from './useTenantPermissions'
import { canViewConfigurationPage } from '../lib/configurationAccess'
import { resolveGovernanceRoutes } from '../governance/resolveRoutes'

/** Configuration management sidebar for organization-level pages. */
export function useNodeSideNav() {
  const { t } = useI18n()
  const { has } = useTenantPermissions()
  return computed<MenuItem[]>(() => {
    const routes = resolveGovernanceRoutes()
    const children = [
      ...resolveGovernanceMenu(t),
      { label: t('settings.nav.subscription'), to: '/node/subscription', icon: CreditCard },
    ].filter(item => canViewConfigurationPage(item.to || '', routes, has))
    return children.length ? [{
      label: t('assetsPage.side.groupGovernance'),
      children,
    }] : []
  })
}
