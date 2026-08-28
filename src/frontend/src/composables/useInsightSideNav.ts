import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { MessageSquare } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { dataGatewaySidebarIcon } from '../lib/resourceIcons'

/** Tenant Insights sidebar: Copilot + tenant Data Gateways only. */
export function useInsightSideNav() {
  const { t } = useI18n()
  return computed<MenuItem[]>(() => [
    {
      label: t('insight.side.groupSmartApps'),
      children: [
        { label: t('insight.side.copilot'), to: '/insight/copilot', icon: MessageSquare },
      ],
    },
    {
      label: t('insight.side.groupDataGateways'),
      children: [
        // Insight Usage is temporarily hidden. Do not delete its page or API implementation.
        // To restore it, add the /insight/usage menu item here and re-enable its route.
        {
          label: t('insight.side.dataGateway'),
          to: '/insight/gateways',
          icon: dataGatewaySidebarIcon,
        },
      ],
    },
  ])
}
