import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Building2, Users, CreditCard } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'

/** Configuration management sidebar for organization-level pages. */
export function useNodeSideNav() {
  const { t } = useI18n()
  return computed<MenuItem[]>(() => [
    {
      label: t('assetsPage.side.groupGovernance'),
      children: [
        { label: t('settings.nav.organizationHub'), to: '/node/organization', icon: Building2 },
        { label: t('settings.nav.members'), to: '/node/members', icon: Users },
        { label: t('settings.nav.subscription'), to: '/node/subscription', icon: CreditCard },
      ],
    },
  ])
}
