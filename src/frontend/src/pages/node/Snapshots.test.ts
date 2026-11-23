// @vitest-environment jsdom
import { flushPromises, shallowMount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import Snapshots from './Snapshots.vue'
import { listBackupSourceSnapshots } from '../../lib/protectionBackupConfigApi'

vi.mock('../../lib/protectionBackupConfigApi', () => ({ listBackupSourceSnapshots: vi.fn() }))
vi.mock('../../composables/useProtectionSideNav', () => ({ useProtectionSideNav: () => [] }))
vi.mock('vue-i18n', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-i18n')>(),
  useI18n: () => ({ t: (key: string) => key }),
}))
vi.mock('vue-router', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-router')>(), onBeforeRouteLeave: vi.fn(),
}))

it('requests the next snapshot page and keeps loading until it returns', async () => {
  const snapshot = { id: 31, snapshot_uid: 'snapshot-31' } as Awaited<ReturnType<typeof listBackupSourceSnapshots>>['results'][number]
  let finishPage: () => void = () => undefined
  const list = vi.mocked(listBackupSourceSnapshots)
    .mockResolvedValueOnce({ count: 31, results: [] })
    .mockImplementationOnce(() => new Promise((resolve) => {
      finishPage = () => resolve({ count: 31, results: [snapshot] })
    }))
  const wrapper = shallowMount(Snapshots, {
    global: {
      stubs: {
        ModulePage: { template: '<div><slot /></div>' },
        ElTable: { name: 'ElTable', props: ['data'], template: '<div />' },
        ElPagination: { name: 'ElPagination', template: '<div />' }, ElTableColumn: true, ElEmpty: true, ElButton: true,
      },
      directives: { loading: vi.fn(), 'table-column-resize': vi.fn() },
    },
  })
  try {
    await flushPromises()
    wrapper.getComponent({ name: 'ElPagination' }).vm.$emit('update:current-page', 2)
    await wrapper.vm.$nextTick()
    expect(list).toHaveBeenLastCalledWith({ page: 2, page_size: 30 }, { signal: expect.any(AbortSignal) })
    expect(wrapper.vm).toMatchObject({ busy: true })
    finishPage()
    await flushPromises()
    expect(wrapper.vm).toMatchObject({ busy: false })
    expect(wrapper.getComponent({ name: 'ElTable' }).props('data')).toEqual([snapshot])
  } finally {
    wrapper.unmount()
  }
})
