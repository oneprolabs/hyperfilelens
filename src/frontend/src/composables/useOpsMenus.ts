import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, BellRing, CircleAlert, Radio, FileText, History, ScrollText } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { tenantOpsObserveMenus } from '@ext/platform/ops/menus'

export function useOpsMenus() {
  const { t } = useI18n()
  return computed<MenuItem[]>(() => {
    const observeChildren = tenantOpsObserveMenus(t)
    const menus: MenuItem[] = []
    if (observeChildren.length) {
      menus.push({
        label: t('ops.nav.groupObserve'),
        children: observeChildren,
      })
    }
    menus.push(
      {
        label: t('ops.nav.groupAttention'),
        children: [
          { label: t('ops.nav.attention'), to: '/ops/attention', icon: CircleAlert },
        ],
      },
      {
        label: t('ops.nav.groupAlerts'),
        children: [
          { label: t('ops.nav.alertIncidents'), to: '/ops/alerts/incidents', icon: AlertTriangle },
          { label: t('ops.nav.alertRules'), to: '/ops/alerts/rules', icon: BellRing },
          { label: t('ops.nav.notificationChannels'), to: '/ops/channels', icon: Radio },
          { label: t('ops.nav.notificationRecords'), to: '/ops/notification-records', icon: ScrollText },
        ],
      },
      {
        label: t('ops.nav.groupEvents'),
        children: [
          { label: t('ops.task.sideAudit'), to: '/ops/audit', icon: FileText },
          { label: t('ops.task.sideTasks'), to: '/ops/task', icon: History },
        ],
      },
    )
    return menus
  })
}
