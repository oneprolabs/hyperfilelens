// @vitest-environment jsdom

import { flushPromises, shallowMount, type VueWrapper } from '@vue/test-utils'
import { ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  directory: vi.fn(),
  status: vi.fn(),
  health: vi.fn(),
  platformList: vi.fn(),
  latest: vi.fn(),
  error: vi.fn(),
  route: { path: '/insight/data-gateways', query: {} },
  lifecyclePatch: null as null | ((rows: unknown[]) => void),
}))

vi.mock('vue-router', async (original) => ({
  ...await original<typeof import('vue-router')>(),
  useRoute: () => mocks.route,
  useRouter: () => ({ replace: vi.fn() }),
  onBeforeRouteLeave: vi.fn(),
}))
vi.mock('vue-i18n', async (original) => ({
  ...await original<typeof import('vue-i18n')>(),
  useI18n: () => ({ t: (key: string) => key }),
}))
vi.mock('../../lib/lensApi', async (original) => ({
  ...await original<typeof import('../../lib/lensApi')>(),
  listLensGatewayDirectory: mocks.directory,
  refreshLensGatewayDirectoryStatus: mocks.status,
  fetchLensHealth: mocks.health,
  listLensGateways: mocks.platformList,
}))
vi.mock('../../lib/nodeApi', async (original) => ({
  ...await original<typeof import('../../lib/nodeApi')>(),
  fetchLatestAgentVersion: mocks.latest,
  updateNode: vi.fn(),
}))
vi.mock('element-plus', async (original) => ({
  ...await original<typeof import('element-plus')>(),
  ElMessage: { error: mocks.error, success: vi.fn(), warning: vi.fn() },
}))
vi.mock('../../composables/useListTableLayout', () => ({
  useListTableLayout: () => ({
    tableMaxHeight: 400, layoutTable: vi.fn(), handleTableScroll: vi.fn(),
  }),
}))
vi.mock('../../composables/useNodeLifecycleOps', () => ({
  useNodeLifecycleOps: (options: { onLifecyclePatch: (rows: unknown[]) => void }) => {
    mocks.lifecyclePatch = options.onLifecyclePatch
    return {
      activeBatchNodeIds: ref(new Set([999])),
      upgradeConfirmOpen: ref(false),
      upgradeConfirmPreview: ref(null),
      lastStartErrors: ref([]),
      snapshot: {},
      restorePersisted: vi.fn(),
      mergeNodeListDuringLifecycleBatch: (rows: unknown[]) => rows,
      canUpgradeNode: () => false,
    }
  },
}))
vi.mock('../../platform-ops/lib/platformOpsApi', async (original) => ({
  ...await original<typeof import('../../platform-ops/lib/platformOpsApi')>(),
  fetchPublicGatewayCapacities: vi.fn().mockResolvedValue({ results: [] }),
}))

import InsightDataGateways from './InsightDataGateways.vue'
import { applyGatewayDirectoryStatus } from '../../lib/gatewayDirectory'
import { isGatewayConnectivityOnline } from '../../lib/gatewayDisplayStatus'
import type { LensGatewayDirectoryStatus, LensGatewayInsight } from '../../lib/lensApi'

function gateway(id = 295): LensGatewayInsight {
  return {
    id, name: `HFL Gateway ${id}`, role: 'gateway', status: 'active',
    ip_address: '192.0.2.1', ai_enabled: true, gateway_link_id: id + 1000,
    sl_lensnode_uuid: `sl-${id}`, lensnode_status: 'online',
    knowledge_source_count: 0, workspace_root: '/workspace',
    sidecar_status: 'online', hfl_agent_online: true, managed_by_hfl: true,
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

const wrappers: VueWrapper[] = []
type DirectoryTestState = {
  rows: LensGatewayInsight[]
  pagination: { page: number; pageSize: number; count: number }
  tableLoading: boolean
  busy: boolean
  search: string
  appliedSearch: string
  bridgeReady: boolean
  load: (options?: { forceStatus?: boolean }) => Promise<void>
  onPaginationPageChange: () => void
  onPaginationSizeChange: () => void
  refreshCurrentPageStatus: (force?: boolean) => void
}

function stateOf(wrapper: VueWrapper): DirectoryTestState {
  return (wrapper.vm.$ as unknown as { setupState: DirectoryTestState }).setupState
}

function mountPage() {
  const wrapper = shallowMount(InsightDataGateways, {
    global: {
      stubs: {
        'el-table': {
          props: ['data'],
          template: '<div class="test-table"><span v-for="row in data" :key="row.id">{{ row.id }}</span></div>',
        },
        HflPagination: { props: ['total', 'currentPage', 'pageSize'], template: '<div />' },
        ElButton: { template: '<button><slot /></button>' },
        ElInput: true, ElTag: true, ElForm: true, ElFormItem: true,
        ElDropdown: true, ElDropdownMenu: true, ElDropdownItem: true,
        'el-table-column': true, 'el-empty': true, 'el-dialog': true,
        'el-option': true, 'el-select': true,
      },
      directives: {
        loading: {}, 'table-overflow-title': {}, 'table-header-scroll-sync': {},
        'table-column-resize': {},
      },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.route.path = '/insight/data-gateways'
  mocks.directory.mockResolvedValue({ count: 15, page: 1, page_size: 30, results: [gateway()] })
  mocks.status.mockResolvedValue([])
  mocks.latest.mockResolvedValue(null)
  mocks.health.mockResolvedValue({ lens: { configured: true, authenticated: true } })
  mocks.platformList.mockResolvedValue([])
})
afterEach(() => {
  for (const wrapper of wrappers.splice(0)) wrapper.unmount()
  vi.useRealTimers()
})

describe('private Data Gateway server pagination', () => {
  it('loads page 1 at 30/page and immediately shows Node 295 without waiting for extras or SL', async () => {
    mocks.latest.mockReturnValue(new Promise(() => {}))
    mocks.health.mockReturnValue(new Promise(() => {}))
    mocks.status.mockReturnValue(new Promise(() => {}))
    const wrapper = mountPage()
    await flushPromises()
    expect(mocks.directory).toHaveBeenCalledWith(expect.objectContaining({
      page: 1, page_size: 30, search: '',
    }))
    expect(wrapper.get('.test-table').text()).toBe('295')
    const state = stateOf(wrapper)
    expect(state.tableLoading).toBe(false)
    expect(state.busy).toBe(false)
    expect(state.pagination.count).toBe(15)
    expect(mocks.status).toHaveBeenCalledWith(expect.objectContaining({ gateway_ids: [295] }))
    expect(mocks.health).not.toHaveBeenCalled()
    expect(mocks.platformList).not.toHaveBeenCalled()
  })

  it('sends target pages and resets page on size/search changes; count is not row length', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    state.pagination.page = 2
    state.onPaginationPageChange()
    await flushPromises()
    expect(mocks.directory).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, page_size: 30 }))
    // Server current-page results are not sliced a second time.
    expect(wrapper.get('.test-table').text()).toBe('295')
    state.pagination.pageSize = 10
    state.onPaginationSizeChange()
    await flushPromises()
    expect(mocks.directory).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1, page_size: 10 }))
    state.pagination.page = 2
    state.search = 'Gateway 295'
    vi.useFakeTimers()
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(mocks.directory).toHaveBeenLastCalledWith(expect.objectContaining({
      page: 1, page_size: 10, search: 'Gateway 295',
    }))
    expect(state.pagination.count).toBe(15)
  })

  it('manual refresh preserves page and size and explicitly refreshes current-page status', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    state.pagination.page = 2
    state.pagination.pageSize = 10
    await wrapper.get('.hfl-refresh-button').trigger('click')
    await flushPromises()
    expect(mocks.directory).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2, page_size: 10 }))
    expect(mocks.status).toHaveBeenLastCalledWith(expect.objectContaining({ gateway_ids: [295], force: true }))
  })

  it('failed status enrichment leaves rows and count intact and does not show list errors', async () => {
    mocks.status.mockRejectedValue(new Error('SL unavailable'))
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.get('.test-table').text()).toBe('295')
    expect(stateOf(wrapper).pagination.count).toBe(15)
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it('failed navigation restores the successful page instead of mislabelling retained rows', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    state.pagination.pageSize = 10
    mocks.directory.mockResolvedValueOnce({
      count: 15, page: 1, page_size: 10, results: [gateway(101)],
    })
    state.onPaginationSizeChange()
    await flushPromises()
    mocks.directory.mockRejectedValueOnce(new Error('directory unavailable'))
    state.pagination.page = 2
    state.onPaginationPageChange()
    await flushPromises()
    expect(state.pagination).toEqual({ page: 1, pageSize: 10, count: 15 })
    expect(wrapper.get('.test-table').text()).toBe('101')
    expect(state.busy).toBe(false)
    expect(mocks.error).toHaveBeenCalledTimes(1)
  })

  it('failed page-size change restores both page and size without reloading', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    state.pagination.page = 2
    state.pagination.pageSize = 10
    mocks.directory.mockResolvedValueOnce({
      count: 15, page: 2, page_size: 10, results: [gateway(102)],
    })
    await state.load()
    await flushPromises()
    mocks.directory.mockRejectedValueOnce(new Error('directory unavailable'))
    state.pagination.pageSize = 30
    state.onPaginationSizeChange()
    await flushPromises()
    expect(state.pagination).toEqual({ page: 2, pageSize: 10, count: 15 })
    expect(wrapper.get('.test-table').text()).toBe('102')
    expect(mocks.directory).toHaveBeenCalledTimes(3)
  })

  it('failed same-page refresh retains its paging conditions and rows', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    mocks.directory.mockRejectedValueOnce(new Error('directory unavailable'))
    await state.load()
    expect(state.pagination).toEqual({ page: 1, pageSize: 30, count: 15 })
    expect(wrapper.get('.test-table').text()).toBe('295')
  })

  it('failed search restores the successful filter without scheduling another request', async () => {
    vi.useFakeTimers()
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    mocks.directory.mockRejectedValueOnce(new Error('directory unavailable'))
    state.search = 'failed search'
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(state.search).toBe('')
    expect(state.appliedSearch).toBe('')
    expect(state.pagination.count).toBe(15)
    expect(wrapper.get('.test-table').text()).toBe('295')
    await vi.advanceTimersByTimeAsync(1000)
    expect(mocks.directory).toHaveBeenCalledTimes(2)
  })

  it('a failed request does not discard a newer unapplied search draft', async () => {
    vi.useFakeTimers()
    const request = deferred<unknown>()
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    mocks.directory.mockReturnValueOnce(request.promise)
    state.search = 'first search'
    await vi.advanceTimersByTimeAsync(900)
    state.search = 'newer draft'
    await flushPromises()
    request.reject(new Error('directory unavailable'))
    await flushPromises()
    expect(state.search).toBe('newer draft')
    expect(state.appliedSearch).toBe('')
    await vi.advanceTimersByTimeAsync(900)
    await flushPromises()
    expect(mocks.directory).toHaveBeenLastCalledWith(expect.objectContaining({
      page: 1, search: 'newer draft',
    }))
  })

  it('a superseded directory failure cannot roll back a newer successful page', async () => {
    const stale = deferred<unknown>()
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    mocks.directory.mockReturnValueOnce(stale.promise)
    state.pagination.page = 2
    const oldLoad = state.load()
    mocks.directory.mockResolvedValueOnce({
      count: 90, page: 3, page_size: 30, results: [gateway(103)],
    })
    state.pagination.page = 3
    await state.load()
    stale.reject(new Error('old failure after cancellation'))
    await oldLoad
    await flushPromises()
    expect(state.pagination).toEqual({ page: 3, pageSize: 30, count: 90 })
    expect(wrapper.get('.test-table').text()).toBe('103')
    expect(mocks.error).not.toHaveBeenCalled()
  })

  it.each([15, 0])('empty page after deletion reloads the last valid page (total %i)', async (count) => {
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    state.pagination.page = 3
    state.pagination.pageSize = 10
    mocks.directory
      .mockResolvedValueOnce({ count, page: 3, page_size: 10, results: [] })
      .mockResolvedValueOnce({ count, page: count ? 2 : 1, page_size: 10, results: count ? [gateway()] : [] })
    await state.load()
    await flushPromises()
    expect(mocks.directory).toHaveBeenLastCalledWith(expect.objectContaining({ page: count ? 2 : 1 }))
    expect(state.pagination.count).toBe(count)
  })

  it('aborts previous status on reload and ignores its late response', async () => {
    const first = deferred<LensGatewayDirectoryStatus[]>()
    const second = deferred<LensGatewayDirectoryStatus[]>()
    mocks.status.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const wrapper = mountPage()
    await flushPromises()
    const oldSignal = mocks.status.mock.calls[0]![0].signal as AbortSignal
    await stateOf(wrapper).load()
    expect(oldSignal.aborted).toBe(true)
    second.resolve([{ ...gateway(), sidecar_status: 'removing' }])
    await flushPromises()
    first.resolve([{ ...gateway(), sidecar_status: 'offline' }])
    await flushPromises()
    expect(stateOf(wrapper).rows[0].sidecar_status).toBe('removing')
    const currentSignal = mocks.status.mock.calls[1]![0].signal as AbortSignal
    // A new pending request is also cancelled on unmount.
    mocks.status.mockReturnValueOnce(new Promise(() => {}))
    stateOf(wrapper).refreshCurrentPageStatus()
    const pending = mocks.status.mock.calls[2]![0].signal as AbortSignal
    wrapper.unmount()
    expect(pending.aborted).toBe(true)
    expect(currentSignal.aborted).toBe(false)
  })

  it('ignores late directory responses and never injects lifecycle rows from other pages', async () => {
    const stale = deferred<unknown>()
    mocks.directory.mockReturnValueOnce(stale.promise)
    const wrapper = mountPage()
    await flushPromises()
    await stateOf(wrapper).load()
    stale.resolve({ count: 99, results: [gateway(111)] })
    await flushPromises()
    expect(wrapper.get('.test-table').text()).toBe('295')
    mocks.lifecyclePatch?.([gateway(999)])
    await flushPromises()
    expect(wrapper.get('.test-table').text()).toBe('295')
    expect(stateOf(wrapper).pagination.count).toBe(15)
  })

  it('periodically refreshes only the displayed private page, not the directory or SL global health', async () => {
    vi.useFakeTimers()
    const wrapper = mountPage()
    await flushPromises()
    await vi.advanceTimersByTimeAsync(30000)
    await flushPromises()
    expect(mocks.directory).toHaveBeenCalledTimes(1)
    expect(mocks.status).toHaveBeenCalledTimes(2)
    expect(mocks.status).toHaveBeenLastCalledWith(expect.objectContaining({ gateway_ids: [295] }))
    expect(mocks.health).not.toHaveBeenCalled()
    expect(stateOf(wrapper).pagination.count).toBe(15)
  })

  it('keeps platform Engine on the existing SL-backed list, not the private directory', async () => {
    mocks.route.path = '/platform-ops/engine/gateways'
    mocks.platformList.mockResolvedValue([gateway()])
    const wrapper = mountPage()
    await flushPromises()
    expect(mocks.platformList).toHaveBeenCalledTimes(1)
    expect(mocks.health).toHaveBeenCalledTimes(1)
    expect(mocks.directory).not.toHaveBeenCalled()
    expect(mocks.status).not.toHaveBeenCalled()
    expect(wrapper.get('.test-table').text()).toBe('295')
  })

  it('platform failure retains cached rows, updates health and suppresses list-error toasts', async () => {
    mocks.route.path = '/platform-ops/engine/gateways'
    mocks.platformList.mockResolvedValueOnce([gateway()])
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    expect(state.bridgeReady).toBe(true)
    mocks.platformList.mockRejectedValueOnce(new Error('SL list unavailable'))
    mocks.health.mockResolvedValueOnce({ lens: { configured: true, authenticated: false } })
    await state.load()
    await flushPromises()
    expect(wrapper.get('.test-table').text()).toBe('295')
    expect(state.bridgeReady).toBe(false)
    expect(state.busy).toBe(false)
    expect(mocks.error).not.toHaveBeenCalled()
    expect(mocks.latest).toHaveBeenCalledTimes(2)
    expect(mocks.directory).not.toHaveBeenCalled()
  })

  it('platform failure with no cached rows still reports the original load error', async () => {
    mocks.route.path = '/platform-ops/engine/gateways'
    mocks.platformList.mockRejectedValueOnce(new Error('SL list unavailable'))
    mocks.health.mockResolvedValueOnce({ lens: { configured: true, authenticated: false } })
    const wrapper = mountPage()
    await flushPromises()
    expect(stateOf(wrapper).bridgeReady).toBe(false)
    expect(mocks.error).toHaveBeenCalledWith({
      message: 'errors.generic.loadFailed', grouping: true,
    })
    expect(stateOf(wrapper).busy).toBe(false)
  })

  it('superseded platform failure cannot overwrite newer health or show a stale error', async () => {
    mocks.route.path = '/platform-ops/engine/gateways'
    mocks.platformList.mockResolvedValueOnce([gateway()])
    const wrapper = mountPage()
    await flushPromises()
    const state = stateOf(wrapper)
    const oldList = deferred<LensGatewayInsight[]>()
    const oldHealth = deferred<unknown>()
    mocks.platformList.mockReturnValueOnce(oldList.promise)
    mocks.health.mockReturnValueOnce(oldHealth.promise)
    const oldLoad = state.load()
    mocks.platformList.mockResolvedValueOnce([gateway(296)])
    await state.load()
    oldList.reject(new Error('old failure'))
    oldHealth.resolve({ lens: { configured: true, authenticated: false } })
    await oldLoad
    await flushPromises()
    expect(state.bridgeReady).toBe(true)
    expect(wrapper.get('.test-table').text()).toBe('296')
    expect(mocks.error).not.toHaveBeenCalled()
  })
})

describe('runtime-only status patches', () => {
  it.each([
    ['online', 'offline', false],
    ['offline', 'online', true],
  ] as const)('refreshes HFL connectivity from %s to %s independently of SL', (before, after, online) => {
    const row = {
      ...gateway(), availability: before, routable: !online,
      hfl_agent_online: !online, lifecycle: { phase: 'stable' },
    }
    const patch = {
      id: row.id, gateway_link_id: row.gateway_link_id,
      sl_lensnode_uuid: row.sl_lensnode_uuid,
      availability: after, routable: online, hfl_agent_online: online,
      availability_updated_at: '2026-10-08T07:00:00Z',
      last_seen_at: '2026-10-08T06:59:00Z',
      // SL sidecar status is not the source of Agent connectivity.
      sl_status: 'online', sidecar_status: 'online',
    }
    const patched = applyGatewayDirectoryStatus([row], [patch])[0]!
    expect(isGatewayConnectivityOnline(patched)).toBe(online)
    expect(patched.hfl_agent_online).toBe(online)
    expect(patched.availability_updated_at).toBe(patch.availability_updated_at)
    expect(patched.last_seen_at).toBe(patch.last_seen_at)
    expect(patched.name).toBe(row.name)
    expect(patched.lifecycle).toEqual(row.lifecycle)
  })

  it('does not override HFL identity/lifecycle or add unrequested rows', () => {
    const row = { ...gateway(), lifecycle: { phase: 'removing' } }
    const malicious = {
      ...gateway(), name: 'SL renamed', status: 'offline',
      lifecycle: null, sidecar_status: 'offline',
    }
    const patched = applyGatewayDirectoryStatus([row], [malicious, gateway(999)])
    expect(patched).toHaveLength(1)
    expect(patched[0]).toMatchObject({
      name: row.name, status: row.status, lifecycle: row.lifecycle,
      sidecar_status: 'offline',
    })
  })
  it('ignores patches from a previous gateway binding', () => {
    const row = gateway()
    expect(applyGatewayDirectoryStatus([row], [{
      ...row, sl_lensnode_uuid: 'old-binding', sidecar_status: 'offline',
    }])[0]).toBe(row)
  })
})
