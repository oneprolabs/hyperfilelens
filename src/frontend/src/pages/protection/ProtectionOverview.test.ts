// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import { router as appRouter } from '../../router'
import Overview from './ProtectionOverview.vue'

const permissions = ref<string[]>([])
vi.mock('../../composables/useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: (key: string) => permissions.value.includes(key) }),
}))
vi.mock('./DataProtection.vue', () => ({
  __esModule: true, default: { template: '<div data-test="wizard" />' },
}))

describe('Unified Protection overview', () => {
  it('uses the original Wizard for configuration-only readers', async () => {
    permissions.value = ['protection.backup_configurations.view']
    const wrapper = mount(Overview)
    await flushPromises()
    expect(wrapper.find('[data-test="wizard"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('reinitializes the same Wizard after Source grants change and unmounts on configuration revocation', async () => {
    permissions.value = ['protection.backup_configurations.view', 'protection.source_nas.view']
    const wrapper = mount(Overview)
    await flushPromises()
    const wizard = wrapper.get('[data-test="wizard"]').element
    permissions.value = ['protection.backup_configurations.view']
    await flushPromises()
    expect(wrapper.get('[data-test="wizard"]').element).not.toBe(wizard)
    permissions.value = []
    await flushPromises()
    expect(wrapper.find('[data-test="wizard"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('redirects old list bookmarks into the Wizard instead of mounting another page', async () => {
    const record = appRouter.getRoutes().find(route => route.path === '/protection/backup-configurations')!
    expect(record.redirect).toBeDefined()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: record.path, redirect: record.redirect! },
        { path: '/protection/backups', component: { template: '<div />' } },
      ],
    })
    await router.push('/protection/backup-configurations?org=tenant-a&id=stale&returnTo=https://example.test')
    expect(router.currentRoute.value.path).toBe('/protection/backups')
    expect(router.currentRoute.value.query).toEqual({ org: 'tenant-a', step: 'start-backup' })
  })
})
