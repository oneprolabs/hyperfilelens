import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { History, MessageSquare } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { useTenantPermissions } from './useTenantPermissions'
import { dataGatewaySidebarIcon } from '../lib/resourceIcons'

/** Tenant Insights sidebar: Copilot + tenant Data Gateways only. */
export function useInsightSideNav() {
  const { t } = useI18n()
  const { has } = useTenantPermissions()
  return computed<MenuItem[]>(() => ([
    {
      label: t('insight.side.groupSmartApps'),
      children: [
        { label: t('insight.side.copilot'), to: '/insight/copilot', icon: MessageSquare },
      ],
    },
    {
      label: t('insight.side.groupDataGateways'),
      children: [
        {
          label: t('insight.side.requestHistory'),
          to: '/insight/usage',
          icon: History,
        },
        {
          label: t('insight.side.dataGateway'),
          to: '/insight/gateways',
          icon: dataGatewaySidebarIcon,
        },
      ],
    },
  ] as MenuItem[]).map((section) => ({
    ...section, children: section.children?.filter((item) => has(
      item.to === '/insight/copilot'
        ? 'insights.chats.view'
        : item.to === '/insight/usage'
          ? 'insights.usage.view'
          : 'insights.private_data_gateways.view',
    )),
  })).filter((section) => section.children?.length))
}
