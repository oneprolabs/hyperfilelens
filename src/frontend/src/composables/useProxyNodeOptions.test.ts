// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { defineComponent, h } from 'vue'
import { getNode, listNodesPaged } from '../lib/nodeApi'
import type { ApiNode } from '../types/node'
import { useProxyNodeOptions } from './useProxyNodeOptions'

vi.mock('../lib/nodeApi', () => ({ getNode: vi.fn(), listNodesPaged: vi.fn() }))
vi.mock('vue-router', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-router')>(),
  onBeforeRouteLeave: vi.fn(),
}))

const node = (id: number) => ({ id, role: 'proxy' }) as ApiNode
type Page = Awaited<ReturnType<typeof listNodesPaged>>

function setup(selectedId?: number) {
  let model!: ReturnType<typeof useProxyNodeOptions>
  const wrapper = mount(defineComponent({
    setup() {
      model = useProxyNodeOptions(() => selectedId, true)
      return () => h('div')
    },
  }))
  return { wrapper, model }
}

describe('Proxy option paging', () => {
  beforeEach(() => vi.resetAllMocks())

  it('loads the next page only on bottom reach and appends without duplicating the selected node', async () => {
    vi.mocked(listNodesPaged)
      .mockResolvedValueOnce({ count: 52, results: Array.from({ length: 50 }, (_, index) => node(index + 1)) })
      .mockResolvedValueOnce({ count: 52, results: [node(51), node(52)] })
    vi.mocked(getNode).mockResolvedValueOnce(node(52))
    const { wrapper, model } = setup(52)
    await model.loadProxyNodes(' proxy ')
    await model.loadMoreProxyNodes('top')
    expect(listNodesPaged).toHaveBeenCalledTimes(1)
    await Promise.all([model.loadMoreProxyNodes('bottom'), model.loadMoreProxyNodes('bottom')])
    expect(model.proxyNodes.value.map((item) => item.id)).toEqual([52, ...Array.from({ length: 51 }, (_, index) => index + 1)])
    await model.loadMoreProxyNodes('bottom')
    expect(listNodesPaged).toHaveBeenCalledTimes(2)
    expect(listNodesPaged).toHaveBeenLastCalledWith({
      role: 'proxy', availability: 'online', page: 2, page_size: 50, search: 'proxy',
    }, expect.anything())
    wrapper.unmount()
  })

  it('discards an old next page when a new search starts', async () => {
    let finishOld!: (value: Page) => void
    const oldPage = new Promise<Page>((resolve) => { finishOld = resolve })
    vi.mocked(listNodesPaged)
      .mockResolvedValueOnce({ count: 52, results: [node(1)] })
      .mockReturnValueOnce(oldPage)
      .mockResolvedValueOnce({ count: 1, results: [node(9)] })
    const { wrapper, model } = setup()
    await model.loadProxyNodes()
    const pending = model.loadMoreProxyNodes('bottom')
    const oldSignal = vi.mocked(listNodesPaged).mock.calls[1][1]?.signal
    await model.loadProxyNodes('new')
    expect(oldSignal?.aborted).toBe(true)
    finishOld({ count: 52, results: [node(2)] })
    await pending
    expect(model.proxyNodes.value).toEqual([node(9)])
    expect(listNodesPaged).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'new', page: 1 }), expect.anything())
    wrapper.unmount()
  })

  it('keeps loaded options when appending fails and retries the same page', async () => {
    vi.mocked(listNodesPaged)
      .mockResolvedValueOnce({ count: 51, results: [node(1)] })
      .mockRejectedValueOnce(new Error('Offline'))
      .mockResolvedValueOnce({ count: 51, results: [node(2)] })
    const { wrapper, model } = setup()
    await model.loadProxyNodes()
    await model.loadMoreProxyNodes('bottom')
    expect(model.proxyNodes.value).toEqual([node(1)])
    await model.loadMoreProxyNodes('bottom')
    expect(model.proxyNodes.value).toEqual([node(1), node(2)])
    expect(vi.mocked(listNodesPaged).mock.calls.map(([params]) => params.page)).toEqual([1, 2, 2])
    wrapper.unmount()
  })
})
