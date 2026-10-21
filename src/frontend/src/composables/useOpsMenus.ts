import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, BellRing, FileText, History, ListTodo, Logs, Radio } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { useTenantPermissions } from './useTenantPermissions'
import { tenantOpsObserveMenus } from '@ext/platform/ops/menus'
import { operationsPages } from '../lib/operationsAccess'

export function useOpsMenus() {
  const { t } = useI18n()
  const { has } = useTenantPermissions()
  return computed<MenuItem[]>(() => {
    const infrastructureMonitoringItems = tenantOpsObserveMenus(t)
    const menus: MenuItem[] = [
      {
        label: t('ops.nav.groupHealthMonitoring'),
        children: [
          ...infrastructureMonitoringItems,
          { label: t('ops.nav.events'), to: '/ops/events', icon: Logs },
        ],
      },
      {
        label: t('ops.nav.groupAlerting'),
        children: [
          { label: t('ops.nav.alerts'), to: '/ops/alerts', icon: AlertTriangle },
          { label: t('ops.nav.alertRules'), to: '/ops/alerts/rules', icon: BellRing },
          { label: t('ops.nav.notificationChannels'), to: '/ops/channels', icon: Radio },
          { label: t('ops.nav.deliveryHistory'), to: '/ops/delivery-history', icon: History },
        ],
      },
      {
        label: t('ops.nav.groupActivity'),
        children: [
          { label: t('ops.task.sideTasks'), to: '/ops/tasks', icon: ListTodo },
          { label: t('ops.task.sideAudit'), to: '/ops/audit-logs', icon: FileText },
        ],
      },
    ]
    return menus.map((section) => ({
      ...section, children: section.children?.filter((item) => {
        const page = operationsPages.find(page => page.path === item.to)
        return page && has(page.permission)
      }),
    })).filter((section) => section.children?.length)
  })
}
