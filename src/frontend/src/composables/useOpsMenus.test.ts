// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useOpsMenus } from './useOpsMenus'

const observeMenus = vi.hoisted(() => ({
  items: [] as Array<{ label: string; to?: string; icon?: unknown }>,
}))

vi.mock('@ext/platform/ops/menus', () => ({
  tenantOpsObserveMenus: () => observeMenus.items,
}))

function mountMenus() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: {
      en: {
        ops: {
          nav: {
            groupHealthMonitoring: 'Health & Monitoring',
            events: 'Events',
            groupAlerting: 'Alerting',
            groupActivity: 'Activity',
            alerts: 'Alerts',
            alertRules: 'Alert Rules',
            notificationChannels: 'Notification Channels',
            deliveryHistory: 'Delivery History',
          },
          task: {
            sideAudit: 'Audit Logs',
            sideTasks: 'Tasks',
          },
        },
      },
    },
  })

  let menus: ReturnType<typeof useOpsMenus> | undefined
  mount(
    defineComponent({
      setup() {
        menus = useOpsMenus()
        return () => null
      },
    }),
    { global: { plugins: [i18n] } },
  )
  return menus!
}

describe('useOpsMenus', () => {
  beforeEach(() => {
    observeMenus.items = []
  })

  it('shows the stable Community groups without Enterprise monitoring', () => {
    const menus = mountMenus()
    const paths = menus.value.flatMap((group) =>
      (group.children || []).map((child) => child.to).filter(Boolean),
    )

    expect(menus.value.map((group) => group.label)).toEqual([
      'Health & Monitoring',
      'Alerting',
      'Activity',
    ])
    expect(paths).not.toContain('/ops/host-monitor')
    expect(paths).toContain('/ops/events')
    expect(paths).toContain('/ops/alerts')
    expect(paths).toContain('/ops/tasks')
    expect(paths).toContain('/ops/audit-logs')
  })

  it('shows System Monitoring when the Enterprise extension contributes it', () => {
    observeMenus.items = [{ label: 'System Monitoring', to: '/ops/host-monitor' }]
    const menus = mountMenus()
    const paths = menus.value.flatMap((group) =>
      (group.children || []).map((child) => child.to).filter(Boolean),
    )

    const healthGroup = menus.value.find((group) => group.label === 'Health & Monitoring')
    expect(healthGroup?.children?.map((item) => item.label)).toEqual([
      'System Monitoring',
      'Events',
    ])
    expect(paths).toContain('/ops/host-monitor')
  })
})
