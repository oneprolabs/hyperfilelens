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
            groupObserve: 'MONITORING',
            monitor: 'Monitor',
            groupAttention: 'ATTENTION',
            attention: 'Attention',
            groupAlerts: 'ALERT CENTER',
            groupEvents: 'AUDIT CENTER',
            alertIncidents: 'Incidents',
            alertRules: 'Alert Policies',
            notificationChannels: 'Notification Channels',
            notificationRecords: 'Notification History',
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

  it('hides Observe/Monitor when the platform extension contributes no observe menus', () => {
    const menus = mountMenus()
    const paths = menus.value.flatMap((group) =>
      (group.children || []).map((child) => child.to).filter(Boolean),
    )

    expect(menus.value.some((group) => group.label === 'MONITORING')).toBe(false)
    expect(paths).not.toContain('/ops/host-monitor')
    expect(paths).toContain('/ops/attention')
    expect(paths).toContain('/ops/alerts/incidents')
    expect(paths).toContain('/ops/task')
  })

  it('shows Observe/Monitor when the platform extension contributes the monitor item', () => {
    observeMenus.items = [{ label: 'Monitor', to: '/ops/host-monitor' }]
    const menus = mountMenus()
    const paths = menus.value.flatMap((group) =>
      (group.children || []).map((child) => child.to).filter(Boolean),
    )

    expect(menus.value.some((group) => group.label === 'MONITORING')).toBe(true)
    expect(paths).toContain('/ops/host-monitor')
  })
})
