// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { nextTick, ref } from 'vue'
import { createI18n } from 'vue-i18n'
import ElementPlus, { ElInput } from 'element-plus'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Directory from './BackupConfigurationDirectory.vue'
import { listBackupConfigs, type BackupConfig } from '../../lib/protectionBackupConfigApi'
import { currentUser, getEffectiveOrgKey, type User } from '../../composables/useAuth'
import { en } from '../../locales/en'
import { backupConfigurationDirectoryPath } from '../../lib/protectionAccess'

const allowed = ref(true)
const cleanups = new Set<() => void>()
const routerMock = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('../../composables/useTenantPermissions', () => ({
  useTenantPermissions: () => ({ has: () => allowed.value }),
}))
vi.mock('../../composables/useProtectionSideNav', () => ({ useProtectionSideNav: () => ref([]) }))
vi.mock('../../lib/protectionBackupConfigApi', () => ({ listBackupConfigs: vi.fn() }))
vi.mock('../../lib/api', () => ({ isAbortError: (error: { name?: string }) => error?.name === 'AbortError' }))
vi.mock('vue-router', () => ({ useRouter: () => routerMock, onBeforeRouteLeave: vi.fn() }))
vi.mock('../../composables/useAuth', async () => {
  const { ref } = await import('vue')
  return { currentUser: ref(null), getEffectiveOrgKey: vi.fn() }
})

function account(orgKey: string): User {
  return {
    id: 7, username: 'reader', email: 'reader@example.test',
    access_profile: {
      org_key: orgKey, role: 'reader',
      permissions: ['protection.backup_configurations.view'], group_permissions: [],
      visible_features: [], available_platforms: [], landing_path: '/',
    },
  }
}

function config(name: string): BackupConfig {
  return {
    id: 7, name, source_type: 'agent', source_ref_id: 3, repository_id: 4,
    repository_endpoint_type: 'external', backup_policy_id: null, file_filter_rule_id: null,
    directory_count: 2, compression_level: 'balanced', recovery_plan_enabled: false,
    created_at: '2026-10-21T01:00:00Z', updated_at: '2026-10-21T01:00:00Z',
  }
}

function open(realPagination = false) {
  const wrapper = mount(Directory, {
    global: {
      plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })],
      directives: { 'table-overflow-title': {} },
      stubs: {
        ModulePage: { template: '<div><slot /></div>' },
        HflTablePanel: { template: '<div><slot name="toolbar-utility" /><slot name="table" :table-max-height="400" /><slot name="footer" /></div>' },
        HflPagination: realPagination ? false : {
          props: ['total', 'currentPage', 'pageSize'],
          emits: ['update:currentPage', 'update:pageSize'],
          template: '<div data-test="pager" :data-page="currentPage">{{ total }} <button data-test="next" @click="$emit(\'update:currentPage\', 2)">Next</button><button data-test="size" @click="$emit(\'update:pageSize\', 50)">Size</button></div>',
        },
      },
    },
  })
  const unmount = wrapper.unmount.bind(wrapper)
  const cleanup = () => {
    cleanups.delete(cleanup)
    unmount()
  }
  wrapper.unmount = cleanup
  cleanups.add(cleanup)
  return wrapper
}

async function click(wrapper: ReturnType<typeof open>, selector: string) {
  // VTU trigger stamps events with Date.now(). A backward clock adjustment can
  // make Vue discard them before the handler runs. Use native button behavior,
  // including disabled handling, without assigning a synthetic Vue timestamp.
  wrapper.get<HTMLButtonElement>(selector).element.click()
  await nextTick()
}

async function searchFor(wrapper: ReturnType<typeof open>, value: string) {
  await wrapper.getComponent(ElInput).setValue(value)
  wrapper.get('input').element.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }))
  await nextTick()
}

describe('Backup Configuration reader without Source inventory access', () => {
  beforeEach(() => {
    allowed.value = true
    routerMock.push.mockReset()
    currentUser.value = account('tenant-a')
    vi.mocked(getEffectiveOrgKey).mockImplementation(() => currentUser.value?.access_profile?.org_key || '')
    vi.mocked(listBackupConfigs).mockReset().mockResolvedValue({ count: 0, results: [] })
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    vi.stubGlobal('matchMedia', (media: string) => ({
      media, matches: media === '(min-width: 1024px)',
      addEventListener() {}, removeEventListener() {},
    }))
  })

  it('opens configuration details with an allowlisted directory return destination', async () => {
    vi.mocked(listBackupConfigs).mockResolvedValue({ count: 1, results: [config('Directory configuration')] })
    const wrapper = open()
    await flushPromises()
    await click(wrapper, '.hfl-table-name-link')
    expect(routerMock.push).toHaveBeenCalledExactlyOnceWith({
      path: '/protection/backups/7', query: { returnTo: backupConfigurationDirectoryPath },
    })
    wrapper.unmount()
  })

  it.each([30, 0])('recovers once through the backend when deletion reduces the total to %s', async nextTotal => {
    let total = 31
    vi.mocked(listBackupConfigs).mockImplementation(async params => {
      if ((params?.page || 1) > Math.max(1, Math.ceil(total / (params?.page_size || 30)))) {
        throw { status: 404, message: 'Invalid page.' }
      }
      return { count: total, results: total ? [config('Remaining configuration')] : [] }
    })
    const wrapper = open()
    await flushPromises()
    await click(wrapper, '[data-test="next"]')
    await flushPromises()
    total = nextTotal
    await click(wrapper, 'button[aria-label="Refresh"]')
    await flushPromises()
    expect(vi.mocked(listBackupConfigs).mock.calls.map(([params]) => params?.page)).toEqual([1, 2, 2, 1])
    expect(wrapper.get('[data-test="pager"]').attributes('data-page')).toBe('1')
    expect(wrapper.get('[data-test="pager"]').text()).toContain(String(nextTotal))
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.find('svg.is-spinning').exists()).toBe(false)
    if (nextTotal) expect(wrapper.text()).toContain('Remaining configuration')
    wrapper.unmount()
  })

  it('does not keep retrying if the first page also returns 404', async () => {
    vi.mocked(listBackupConfigs).mockResolvedValue({ count: 31, results: [config('Configuration')] })
    const wrapper = open()
    await flushPromises()
    await click(wrapper, '[data-test="next"]')
    await flushPromises()
    vi.mocked(listBackupConfigs).mockRejectedValue({ status: 404, message: 'Not found.' })
    await click(wrapper, 'button[aria-label="Refresh"]')
    await flushPromises()
    expect(vi.mocked(listBackupConfigs).mock.calls.map(([params]) => params?.page)).toEqual([1, 2, 2, 1])
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    expect(wrapper.find('svg.is-spinning').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each([401, 403, 503])('does not reinterpret %s on a later page as a pagination failure', async status => {
    vi.mocked(listBackupConfigs).mockResolvedValue({ count: 31, results: [config('Configuration')] })
    const wrapper = open()
    await flushPromises()
    await click(wrapper, '[data-test="next"]')
    await flushPromises()
    vi.mocked(listBackupConfigs).mockRejectedValue({ status, message: 'Request failed.' })
    await click(wrapper, 'button[aria-label="Refresh"]')
    await flushPromises()
    expect(vi.mocked(listBackupConfigs).mock.calls.map(([params]) => params?.page)).toEqual([1, 2, 2])
    expect(wrapper.get('[data-test="pager"]').attributes('data-page')).toBe('2')
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('does not let an old organization pagination error reset the new organization page', async () => {
    vi.mocked(listBackupConfigs).mockResolvedValue({ count: 93, results: [config('Configuration')] })
    const wrapper = open()
    await flushPromises()
    let rejectOld!: (reason: unknown) => void
    vi.mocked(listBackupConfigs).mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject }))
    await click(wrapper, '[data-test="next"]')
    currentUser.value = account('tenant-b')
    await flushPromises()
    await click(wrapper, '[data-test="next"]')
    await flushPromises()
    const calls = vi.mocked(listBackupConfigs).mock.calls.length
    rejectOld({ status: 404, message: 'Invalid page.' })
    await flushPromises()
    expect(wrapper.get('[data-test="pager"]').attributes('data-page')).toBe('2')
    expect(listBackupConfigs).toHaveBeenCalledTimes(calls)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })
  afterEach(() => {
    for (const cleanup of [...cleanups]) cleanup()
    vi.unstubAllGlobals()
  })

  it('uses server rows and count, not Source inventory or first-page filtering', async () => {
    vi.mocked(listBackupConfigs).mockResolvedValue({
      count: 93,
      results: [config('Group backup')],
    })
    const wrapper = open()
    await flushPromises()
    expect(wrapper.text()).toContain('Group backup')
    expect(wrapper.get('[data-test="pager"]').text()).toContain('93')
    expect(listBackupConfigs).toHaveBeenLastCalledWith(
      { page: 1, page_size: 30, search: undefined, ordering: '-created_at' },
      { signal: expect.any(AbortSignal) },
    )
    await click(wrapper, '[data-test="next"]')
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2 }), expect.anything(),
    )
    await click(wrapper, '[data-test="size"]')
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, page_size: 50 }), expect.anything(),
    )
    await searchFor(wrapper, 'report')
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, search: 'report' }), expect.anything(),
    )
    wrapper.unmount()
  })

  it.each([false, true])('uses real pagination and search controls with a backward clock adjustment: %s', async backwardClock => {
    vi.mocked(listBackupConfigs).mockImplementation(async params => ({
      count: params?.search ? 1 : 93,
      results: [config(params?.search ? 'Search result' : `Page ${params?.page}`)],
    }))
    const wrapper = open(true)
    await flushPromises()
    const clock = backwardClock ? vi.spyOn(Date, 'now').mockReturnValue(Date.now() - 1000) : undefined
    try {
      await click(wrapper, '.btn-next')
      await flushPromises()
      expect(listBackupConfigs).toHaveBeenLastCalledWith(
        { page: 2, page_size: 30, search: undefined, ordering: '-created_at' },
        { signal: expect.any(AbortSignal) },
      )
      expect(wrapper.text()).toContain('Page 2')
      expect(wrapper.get('.el-pager .is-active').text()).toBe('2')
      expect(wrapper.get('.el-pagination__total').text()).toContain('93')

      // Exercise native input and Enter, not just the component's model event.
      const input = wrapper.get<HTMLInputElement>('input[aria-label="Search"]')
      input.element.value = 'report'
      input.element.dispatchEvent(new Event('input', { bubbles: true }))
      await nextTick()
      input.element.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }))
      await flushPromises()
      expect(listBackupConfigs).toHaveBeenLastCalledWith(
        { page: 1, page_size: 30, search: 'report', ordering: '-created_at' },
        { signal: expect.any(AbortSignal) },
      )
      expect(wrapper.get('.el-pager .is-active').text()).toBe('1')
      expect(wrapper.get('.el-pagination__total').text()).toContain('1')
      expect(wrapper.text()).toContain('Search result')
      expect(vi.mocked(listBackupConfigs).mock.calls.map(([params]) => [params?.page, params?.search]))
        .toEqual([[1, undefined], [2, undefined], [1, 'report']])
    } finally {
      clock?.mockRestore()
      wrapper.unmount()
    }
  })

  it('shows denied rather than an empty list, clears loading, and makes no automatic retries', async () => {
    vi.mocked(listBackupConfigs).mockRejectedValue({ status: 403, code: 'AUTH.FORBIDDEN' })
    const wrapper = open()
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain(en.errors.codes.authForbidden)
    expect(wrapper.find('svg.is-spinning').exists()).toBe(false)
    expect(wrapper.get('button[aria-label="Refresh"]').attributes('disabled')).toBeUndefined()
    await click(wrapper, '[data-test="next"]')
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('retries a denied read only on explicit Refresh, without an automatic retry loop', async () => {
    vi.mocked(listBackupConfigs).mockRejectedValueOnce({ status: 403, code: 'AUTH.FORBIDDEN' })
    const wrapper = open()
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenCalledTimes(1)
    await click(wrapper, 'button[aria-label="Refresh"]')
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('clears an old 403 when permission is revoked and granted again', async () => {
    vi.mocked(listBackupConfigs).mockRejectedValueOnce({ status: 403, code: 'AUTH.FORBIDDEN' })
    const wrapper = open()
    await flushPromises()
    allowed.value = false
    await flushPromises()
    allowed.value = true
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('recovers from a denied read when verified grants change without changing the view hint', async () => {
    vi.mocked(listBackupConfigs).mockRejectedValueOnce({ status: 403, code: 'AUTH.FORBIDDEN' })
    const wrapper = open()
    await flushPromises()
    currentUser.value!.access_profile!.group_permissions = ['protection.backup_configurations.view']
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)

    // Replacing the same session snapshot is not another authorization grant.
    currentUser.value = JSON.parse(JSON.stringify(currentUser.value)) as User
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('does not carry a denied state into another organization with the same permissions', async () => {
    vi.mocked(listBackupConfigs).mockRejectedValueOnce({ status: 403, code: 'AUTH.FORBIDDEN' })
    const wrapper = open()
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    vi.mocked(listBackupConfigs).mockResolvedValueOnce({ count: 1, results: [config('Organization b')] })
    currentUser.value = account('tenant-b')
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Organization b')
    wrapper.unmount()
  })

  it('resets search and pagination, aborts the old organization, and ignores its late response', async () => {
    vi.mocked(listBackupConfigs).mockResolvedValue({ count: 93, results: [config('Organization a')] })
    const wrapper = open()
    await flushPromises()
    await searchFor(wrapper, 'report')
    await flushPromises()
    let resolveOld!: (value: Awaited<ReturnType<typeof listBackupConfigs>>) => void
    let resolveNew!: (value: Awaited<ReturnType<typeof listBackupConfigs>>) => void
    vi.mocked(listBackupConfigs).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    await click(wrapper, '[data-test="next"]')
    const oldSignal = vi.mocked(listBackupConfigs).mock.lastCall?.[1]?.signal
    const beforeSwitch = vi.mocked(listBackupConfigs).mock.calls.length
    vi.mocked(listBackupConfigs).mockImplementationOnce(() => new Promise(resolve => { resolveNew = resolve }))
    currentUser.value = account('tenant-b')
    await flushPromises()
    expect(oldSignal?.aborted).toBe(true)
    expect(wrapper.text()).not.toContain('Organization a')
    expect(wrapper.get('input').element.value).toBe('')
    expect(listBackupConfigs).toHaveBeenCalledTimes(beforeSwitch + 1)
    expect(listBackupConfigs).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 1, search: undefined }), expect.anything(),
    )

    resolveOld({ count: 999, results: [config('Late organization a')] })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Late organization a')
    expect(wrapper.find('svg.is-spinning').exists()).toBe(true)
    resolveNew({ count: 1, results: [config('Organization b')] })
    await flushPromises()
    expect(wrapper.text()).toContain('Organization b')
    expect(wrapper.get('[data-test="pager"]').text()).not.toContain('999')
    wrapper.unmount()
  })

  it('rejects a response after storage changes even before the new verified profile arrives', async () => {
    let resolveOld!: (value: Awaited<ReturnType<typeof listBackupConfigs>>) => void
    vi.mocked(listBackupConfigs).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    const wrapper = open()
    vi.mocked(getEffectiveOrgKey).mockReturnValue('tenant-b')
    resolveOld({ count: 999, results: [config('Old organization')] })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Old organization')
    expect(wrapper.get('[data-test="pager"]').text()).not.toContain('999')
    currentUser.value = account('tenant-b')
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenCalledTimes(2)
    expect(wrapper.find('svg.is-spinning').exists()).toBe(false)
    wrapper.unmount()
  })

  it('offers explicit Retry on a transient read failure', async () => {
    vi.mocked(listBackupConfigs).mockRejectedValueOnce({ status: 503, code: 'SERVER.ERROR' })
    const wrapper = open()
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('Failed to load backup configurations.')
    await click(wrapper, '[role="alert"] button')
    await flushPromises()
    expect(listBackupConfigs).toHaveBeenCalledTimes(2)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('ignores a stale response after a newer page request', async () => {
    let resolveFirst!: (value: Awaited<ReturnType<typeof listBackupConfigs>>) => void
    vi.mocked(listBackupConfigs).mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
    const wrapper = open()
    await nextTick()
    const firstSignal = vi.mocked(listBackupConfigs).mock.calls[0][1]?.signal
    await click(wrapper, '[data-test="next"]')
    await flushPromises()
    expect(firstSignal?.aborted).toBe(true)
    resolveFirst({ count: 999, results: [config('Stale data')] })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Stale data')
    expect(wrapper.get('[data-test="pager"]').text()).not.toContain('999')
    wrapper.unmount()
  })

  it('aborts pending reads on unmount and clears displayed rows when hints are revoked', async () => {
    const wrapper = open()
    await flushPromises()
    allowed.value = false
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain(en.errors.codes.authForbidden)
    expect(listBackupConfigs).toHaveBeenCalledTimes(1)
    wrapper.unmount()

    allowed.value = true
    vi.mocked(listBackupConfigs).mockImplementationOnce(() => new Promise(() => {}))
    const pending = open()
    const signal = vi.mocked(listBackupConfigs).mock.lastCall?.[1]?.signal
    pending.unmount()
    expect(signal?.aborted).toBe(true)
  })
})
