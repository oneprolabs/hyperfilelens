// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import { useAppPrimaryNav } from './useAppPrimaryNav'
import { useProtectionSideNav } from './useProtectionSideNav'

const permissions = ref<string[]>([])
vi.mock('vue-router', () => ({ useRoute: () => ({ path: '/protection/backups' }) }))
vi.mock('./useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: (key: string) => permissions.value.includes(key) }),
}))

describe('Protection navigation', () => {
  beforeEach(() => { permissions.value = [] })
  it('reacts to access grants and hides empty sidebar sections', () => {
    let primary!: ReturnType<typeof useAppPrimaryNav>
    let sidebar!: ReturnType<typeof useProtectionSideNav>
    const wrapper = mount(defineComponent({
      setup() {
        primary = useAppPrimaryNav()
        sidebar = useProtectionSideNav()
        return () => h('div')
      },
    }), { global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] } })
    expect(primary.items.value.some(item => item.to === '/protection')).toBe(false)
    expect(sidebar.value).toEqual([])
    permissions.value = ['protection.source_nas.view']
    expect(primary.itemsWithActiveState.value.some(item => item.to === '/protection')).toBe(true)
    expect(sidebar.value.flatMap(section => section.children?.map(item => item.to) || []))
      .toEqual(['/protection/backup-sources?tab=nas'])
    permissions.value = ['protection.backup_configurations.view']
    expect(sidebar.value.flatMap(section => section.children?.map(item => item.label) || []))
      .toEqual(['Backup Configurations'])
    expect(sidebar.value[0].children?.[0].to).toBe('/protection/backup-configurations')
    permissions.value.push('protection.source_hosts.view')
    expect(sidebar.value[0].children?.[0].label).toBe(en.protection.side.dataProtection)
    expect(sidebar.value[0].children?.map(item => item.to))
      .toEqual(['/protection/backups', '/protection/backup-configurations'])
    permissions.value = []
    expect(sidebar.value).toEqual([])
    expect(primary.items.value.some(item => item.to === '/protection')).toBe(false)
    wrapper.unmount()
  })
})
