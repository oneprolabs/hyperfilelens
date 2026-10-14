import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, BellRing, FileText, History, ListTodo, Logs, Radio } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { useTenantPermissions } from './useTenantPermissions'
import { tenantOpsObserveMenus } from '@ext/platform/ops/menus'

export function useOpsMenus() {
  const { t } = useI18n()
  const { has } = useTenantPermissions()
  const required: Record<string, string> = {
    '/ops/host-monitor': 'operations.host_monitoring.view',
    '/ops/events': 'operations.events.view', '/ops/alerts': 'operations.alerts.view',
    '/ops/alerts/rules': 'operations.alert_rules.view', '/ops/channels': 'operations.notification_channels.view',
    '/ops/delivery-history': 'operations.notification_records.view', '/ops/tasks': 'operations.tasks.view',
    '/ops/audit-logs': 'operations.audit_logs.view',
  }
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
      ...section, children: section.children?.filter((item) => !required[item.to || ''] || has(required[item.to || '']!)),
    })).filter((section) => section.children?.length)
  })
}
