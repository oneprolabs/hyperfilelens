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
vi.mock('./BackupConfigurationDirectory.vue', () => ({
  __esModule: true, default: { template: '<div data-test="directory" />' },
}))
vi.mock('./DataProtection.vue', () => ({
  __esModule: true, default: { template: '<div data-test="wizard" />' },
}))

describe('Protection overview without expanding backend access', () => {
  it('mounts the directory, not the Source-dependent Wizard, for a config-only reader', async () => {
    permissions.value = ['protection.backup_configurations.view']
    const wrapper = mount(Overview)
    await flushPromises()
    expect(wrapper.find('[data-test="directory"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="wizard"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('retains the existing Wizard with Source view access and unmounts it on revocation', async () => {
    permissions.value = ['protection.backup_configurations.view', 'protection.source_nas.view']
    const wrapper = mount(Overview)
    await flushPromises()
    expect(wrapper.find('[data-test="wizard"]').exists()).toBe(true)
    permissions.value = ['protection.backup_configurations.view']
    await flushPromises()
    expect(wrapper.find('[data-test="wizard"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="directory"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('keeps the independently routed directory mounted when an unrelated Source Group grant is added', async () => {
    const record = appRouter.getRoutes().find(route => route.path === '/protection/backup-configurations')
    expect(record?.components?.default).toBeDefined()
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{
        path: '/protection/backup-configurations',
        component: record!.components!.default,
      }],
    })
    permissions.value = ['protection.backup_configurations.view']
    await router.push('/protection/backup-configurations')
    const wrapper = mount({ template: '<RouterView />' }, { global: { plugins: [router] } })
    await flushPromises()
    expect(wrapper.find('[data-test="directory"]').exists()).toBe(true)
    permissions.value.push('protection.source_nas.view')
    await flushPromises()
    expect(wrapper.find('[data-test="directory"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="wizard"]').exists()).toBe(false)
    expect(router.currentRoute.value.path).toBe('/protection/backup-configurations')
    wrapper.unmount()
  })
})
