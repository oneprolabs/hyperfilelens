// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import { useAppPrimaryNav } from './useAppPrimaryNav'
import { useOpsMenus } from './useOpsMenus'
import { operationsPages } from '../lib/operationsAccess'

const organization = ref<string[]>([])
const group = ref<string[]>([])
const monitorAvailable = ref(true)
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/ops/tasks' }) }))
vi.mock('../ops/resolveRoutes', () => ({ hasTenantHostMonitor: () => monitorAvailable.value }))
vi.mock('@ext/platform/ops/menus', () => ({
  tenantOpsObserveMenus: () => monitorAvailable.value ? [{ to: '/ops/host-monitor', label: 'System Monitoring' }] : [],
}))
vi.mock('./useTenantPermissions', () => ({
  useTenantPermissions: () => ({
    has: (key: string) => organization.value.includes(key) || group.value.includes(key),
  }),
}))

function navigation() {
  let primary!: ReturnType<typeof useAppPrimaryNav>
  let sidebar!: ReturnType<typeof useOpsMenus>
  const wrapper = mount(defineComponent({
    setup() {
      primary = useAppPrimaryNav()
      sidebar = useOpsMenus()
      return () => null
    },
  }), { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
  return { primary, sidebar, wrapper }
}

describe('Operations navigation', () => {
  beforeEach(() => { organization.value = []; group.value = []; monitorAvailable.value = true })

  it('hides both primary navigation surfaces and all empty sidebar groups without Operations access', () => {
    organization.value = ['configuration.roles.manage']
    const { primary, sidebar, wrapper } = navigation()
    expect(primary.items.value.some(item => item.to === '/ops')).toBe(false)
    expect(primary.itemsWithActiveState.value.some(item => item.to === '/ops')).toBe(false)
    expect(sidebar.value).toEqual([])
    wrapper.unmount()
  })

  it.each(operationsPages)('shows only $path and responds to Group grant revocation', page => {
    const { primary, sidebar, wrapper } = navigation()
    group.value = [page.permission]
    expect(primary.items.value.some(item => item.to === '/ops')).toBe(true)
    expect(sidebar.value.flatMap(section => section.children?.map(item => item.to) || [])).toEqual([page.path])
    group.value = []
    expect(primary.itemsWithActiveState.value.some(item => item.to === '/ops')).toBe(false)
    expect(sidebar.value).toEqual([])
    wrapper.unmount()
  })

  it('does not advertise an unavailable Enterprise monitoring route', () => {
    monitorAvailable.value = false
    organization.value = ['operations.host_monitoring.view']
    const { primary, sidebar, wrapper } = navigation()
    expect(primary.items.value.some(item => item.to === '/ops')).toBe(false)
    expect(sidebar.value).toEqual([])
    organization.value.push('operations.tasks.view')
    expect(primary.items.value.some(item => item.to === '/ops')).toBe(true)
    expect(sidebar.value.flatMap(section => section.children?.map(item => item.to) || [])).toEqual(['/ops/tasks'])
    wrapper.unmount()
  })
})
