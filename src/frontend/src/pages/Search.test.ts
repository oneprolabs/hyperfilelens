// @vitest-environment jsdom
import { flushPromises, shallowMount } from '@vue/test-utils'
import { reactive } from 'vue'
import { expect, it, vi } from 'vitest'
import Search from './Search.vue'
import { api } from '../lib/api'

vi.mock('../lib/api', () => ({ api: vi.fn(), isAbortError: () => false }))
vi.mock('vue-i18n', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-i18n')>(),
  useI18n: () => ({ t: (key: string) => key }),
}))
vi.mock('vue-router', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-router')>(),
  useRoute: () => route,
  onBeforeRouteLeave: vi.fn(),
}))

const route = reactive({ query: { q: '' } })

it('requests only the selected section when opening another result page', async () => {
  route.query.q = 'host'
  const mockApi = vi.mocked(api).mockImplementation(async (path) => (
    String(path).includes('/node/nodes/') ? { count: 21, results: [{ id: 1, name: 'host' }] } : { results: [] }
  ) as never)
  const wrapper = shallowMount(Search, {
    global: {
      stubs: {
        ModulePage: { template: '<div><slot /></div>' },
        HflPagination: { name: 'HflPagination', template: '<button @click="$emit(\'update:currentPage\', 2)" />' },
        ElTable: true, ElInput: true, ElTableColumn: true, ElButton: true, ElEmpty: true, ElTag: true,
      },
      directives: { loading: vi.fn() },
    },
  })
  try {
    await flushPromises()
    mockApi.mockClear()
    await wrapper.getComponent({ name: 'HflPagination' }).trigger('click')
    await flushPromises()
    expect(mockApi).toHaveBeenCalledTimes(1)
    expect(mockApi).toHaveBeenCalledWith(expect.stringContaining('/node/nodes/?page=2&page_size=20&search=host'), expect.anything())
  } finally {
    wrapper.unmount()
    route.query.q = ''
  }
})

it('sends one request per section and retains backend matches outside the displayed fields', async () => {
  vi.useFakeTimers()
  let finishSearch: (value: unknown) => void = () => undefined
  const snapshot = { id: 91, snapshot_uid: 'snapshot-91', status: 'available' }
  const mockApi = vi.mocked(api).mockImplementation(async (path) => {
    if (String(path).includes('backup-source-snapshots') && String(path).includes('search=directory')) {
      return new Promise((resolve) => { finishSearch = resolve }) as never
    }
    return { results: [] } as never
  })
  const wrapper = shallowMount(Search, {
    global: {
      stubs: {
        ModulePage: { template: '<div><slot /></div>' },
        ElTable: { name: 'ElTable', props: ['data'], template: '<div />' },
        ElInput: true, ElTableColumn: true, ElButton: true, ElEmpty: true, ElTag: true,
      },
      directives: { loading: vi.fn() },
    },
  })
  try {
    await flushPromises()
    mockApi.mockClear()
    route.query.q = 'directory'
    await wrapper.vm.$nextTick()
    await vi.advanceTimersByTimeAsync(900)
    expect(mockApi).toHaveBeenCalledTimes(5)
    expect(mockApi.mock.calls.filter(([path]) => String(path).includes('/alerts/records/'))).toHaveLength(1)
    expect(wrapper.vm).toMatchObject({ busy: true })
    finishSearch({ results: [snapshot] })
    await flushPromises()
    expect(wrapper.vm).toMatchObject({ busy: false })
    expect(wrapper.findAllComponents({ name: 'ElTable' })[3].props('data')).toEqual([snapshot])
  } finally {
    wrapper.unmount()
    vi.useRealTimers()
  }
})
