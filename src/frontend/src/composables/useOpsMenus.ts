import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, BellRing, FileText, ListTodo, Logs, Radio, ScrollText } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { tenantOpsObserveMenus } from '@ext/platform/ops/menus'

export function useOpsMenus() {
  const { t } = useI18n()
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
          { label: t('ops.nav.deliveryHistory'), to: '/ops/delivery-history', icon: ScrollText },
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
    return menus
  })
}
