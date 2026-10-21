import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { History, MessageSquare } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { useTenantPermissions } from './useTenantPermissions'
import { dataGatewaySidebarIcon } from '../lib/resourceIcons'
import { insightPages } from '../lib/insightAccess'

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
    ...section, children: section.children?.filter((item) => {
      const page = insightPages.find(page => page.path === item.to)
      return page && has(page.permission, page.organizationOnly)
    }),
  })).filter((section) => section.children?.length))
}
