import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { CreditCard } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { resolveGovernanceMenu } from '../governance/resolveMenu'

/** Configuration management sidebar for organization-level pages. */
export function useNodeSideNav() {
  const { t } = useI18n()
  return computed<MenuItem[]>(() => [
    {
      label: t('assetsPage.side.groupGovernance'),
      children: [
        ...resolveGovernanceMenu(t),
        { label: t('settings.nav.subscription'), to: '/node/subscription', icon: CreditCard },
      ],
    },
  ])
}
