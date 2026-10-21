// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus from 'element-plus'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import BackupDetail from './BackupDetail.vue'
import { getBackupConfig, type BackupConfigDetail } from '../../lib/protectionBackupConfigApi'
import { backupConfigurationDirectoryPath } from '../../lib/protectionAccess'
import { en } from '../../locales/en'

vi.mock('../../composables/useProtectionSideNav', () => ({ useProtectionSideNav: () => ref([]) }))
vi.mock('../../composables/useResourceCapabilities', () => ({
  useResourceCapabilities: () => ({ permitted: () => false }),
}))
vi.mock('../../lib/api', () => ({
  apiErrorMessage: (error: { message?: string }) => error?.message || 'Failed to load',
}))
vi.mock('../../composables/useAuth', async () => {
  const { ref } = await import('vue')
  return { currentUser: ref(null), getEffectiveOrgKey: () => 'tenant-a' }
})
vi.mock('../../lib/protectionBackupConfigApi', async load => ({
  ...await load<typeof import('../../lib/protectionBackupConfigApi')>(),
  getBackupConfig: vi.fn(),
  listBackupSourceSnapshots: vi.fn(async () => ({ count: 0, results: [] })),
}))
vi.mock('../../lib/storageRepositoryApi', async load => ({
  ...await load<typeof import('../../lib/storageRepositoryApi')>(),
  listAllStorageRepositories: vi.fn(async () => []),
}))
vi.mock('../../lib/protectionPolicyApi', async load => ({
  ...await load<typeof import('../../lib/protectionPolicyApi')>(),
  listBackupPolicies: vi.fn(async () => ({ count: 0, results: [] })),
  listFileFilterRules: vi.fn(async () => ({ count: 0, results: [] })),
}))
vi.mock('../../lib/sourceApi', async load => ({
  ...await load<typeof import('../../lib/sourceApi')>(),
  listBackupSelectableSources: vi.fn(async () => ({ count: 0, results: [] })),
}))
vi.mock('../../lib/taskApi', async load => ({
  ...await load<typeof import('../../lib/taskApi')>(),
  listTasks: vi.fn(async () => ({ count: 0, results: [] })),
}))

const config: BackupConfigDetail = {
  id: 7, name: 'Directory configuration', source_type: 'agent', source_ref_id: 3,
  repository_id: 4, repository_endpoint_type: 'external', backup_policy_id: null,
  file_filter_rule_id: null, directory_count: 0, compression_level: 'balanced',
  recovery_plan_enabled: false, remark: '', directories: [], recovery_plans: [],
  created_at: '2026-10-21T01:00:00Z', updated_at: '2026-10-21T01:00:00Z',
}

describe('Backup detail return navigation', () => {
  beforeEach(() => {
    vi.mocked(getBackupConfig).mockReset().mockResolvedValue(config)
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  })
  afterEach(() => vi.unstubAllGlobals())

  it.each([
    ['loaded', backupConfigurationDirectoryPath, backupConfigurationDirectoryPath],
    ['failed', backupConfigurationDirectoryPath, backupConfigurationDirectoryPath],
    ['loaded', undefined, '/protection/backups'],
    ['failed', undefined, '/protection/backups'],
    ['loaded', 'https://example.test', '/protection/backups'],
    ['failed', '//example.test', '/protection/backups'],
  ])('returns from the %s view with source %s to %s', async (state, returnTo, expected) => {
    if (state === 'failed') {
      vi.mocked(getBackupConfig).mockRejectedValue({ status: 404, message: 'Not found.' })
    }
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/protection/backups/:backupId', component: { template: '<div />' } },
        { path: '/protection/backups', component: { template: '<div />' } },
        { path: backupConfigurationDirectoryPath, component: { template: '<div />' } },
      ],
    })
    await router.push({ path: '/protection/backups/7', query: { returnTo } })
    const wrapper = mount(BackupDetail, {
      global: {
        plugins: [router, ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })],
        directives: { 'table-overflow-title': {}, 'table-column-resize': {} },
        stubs: {
          ModulePage: { template: '<main><slot /></main>' },
          ElDrawer: true, ElTable: true, ElTableColumn: true,
          Modal: true, DangerConfirmDialog: true,
        },
      },
    })
    await flushPromises()
    const back = wrapper.get(`a[href="${expected}"]`)
    if (expected === backupConfigurationDirectoryPath) {
      expect(back.text()).toBe(en.protection.backupDetail.backToList)
    }
    await back.trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe(expected)
    wrapper.unmount()
  })
})
