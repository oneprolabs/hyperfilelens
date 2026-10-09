// @vitest-environment jsdom
import { defineComponent, nextTick, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { listSourceTagFilterOptions, type SourceTag } from '../../../lib/sourceApi'
import { SOURCE_TAG_FILTER_LIMIT, useSourceTagFilter } from './useSourceTagFilter'

vi.mock('../../../lib/sourceApi', () => ({ listSourceTagFilterOptions: vi.fn() }))

function deferred() {
  let resolve!: (value: { tags: SourceTag[]; untaggedSourceCount: number }) => void
  let reject!: (reason: Error) => void
  const promise = new Promise<{ tags: SourceTag[]; untaggedSourceCount: number }>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

const tag = (id: number, name = `Tag ${id}`): SourceTag => ({
  id, name, description: '', color: 'neutral', source_count: 1,
})
const wrappers: Array<ReturnType<typeof mount>> = []
function setup() {
  const selected = ref([1, 2])
  const draft = ref([1, 2])
  let state!: ReturnType<typeof useSourceTagFilter>
  const wrapper = mount(defineComponent({
    setup() {
      state = useSourceTagFilter(selected, draft)
      return state
    },
    template: `<div>
      <span v-if="tagFilterLoading">Loading</span>
      <button v-if="tagFilterError" @click="refreshTagFilterOptions">Retry</button>
      <span v-for="tag in availableSourceTags" :key="tag.id">{{ tag.name }}</span>
    </div>`,
  }))
  wrappers.push(wrapper)
  return { wrapper, state, selected, draft }
}

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  vi.resetAllMocks()
})

describe('source tag filter refresh lifecycle', () => {
  it('shows loading until completion and prunes deleted tags from applied and draft selections', async () => {
    const request = deferred()
    vi.mocked(listSourceTagFilterOptions).mockReturnValueOnce(request.promise)
    const { wrapper, state, selected, draft } = setup()
    await nextTick()
    expect(wrapper.text()).toContain('Loading')
    request.resolve({ tags: [tag(2, 'Renamed')], untaggedSourceCount: 3 })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Loading')
    expect(wrapper.text()).toContain('Renamed')
    expect(state.untaggedSourceCount.value).toBe(3)
    expect(selected.value).toEqual([2])
    expect(draft.value).toEqual([2])
  })

  it('distinguishes failure from empty data and supports a delayed retry', async () => {
    vi.mocked(listSourceTagFilterOptions).mockRejectedValueOnce(new Error('Offline'))
    const { wrapper, state, selected } = setup()
    await flushPromises()
    expect(wrapper.find('button').text()).toBe('Retry')
    expect(selected.value).toEqual([1, 2])
    const retry = deferred()
    vi.mocked(listSourceTagFilterOptions).mockReturnValueOnce(retry.promise)
    await wrapper.find('button').trigger('click')
    expect(state.tagFilterLoading.value).toBe(true)
    retry.resolve({ tags: [], untaggedSourceCount: 0 })
    await flushPromises()
    expect(state.tagFilterError.value).toBe(false)
    expect(state.tagFilterLoading.value).toBe(false)
    expect(selected.value).toEqual([])
  })

  it('ignores out-of-order results and aborts the superseded request', async () => {
    const old = deferred()
    const latest = deferred()
    vi.mocked(listSourceTagFilterOptions)
      .mockReturnValueOnce(old.promise).mockReturnValueOnce(latest.promise)
    const { state } = setup()
    const oldSignal = vi.mocked(listSourceTagFilterOptions).mock.calls[0][0]?.signal
    const refresh = state.refreshTagFilterOptions()
    expect(oldSignal?.aborted).toBe(true)
    old.resolve({ tags: [tag(1, 'Stale')], untaggedSourceCount: 99 })
    await flushPromises()
    expect(state.tagFilterLoading.value).toBe(true)
    expect(state.availableSourceTags.value).toEqual([])
    latest.resolve({ tags: [tag(2)], untaggedSourceCount: 2 })
    await refresh
    expect(state.untaggedSourceCount.value).toBe(2)
  })

  it('refreshes changed counts and new tags after mutation or returning from tag management', async () => {
    vi.mocked(listSourceTagFilterOptions)
      .mockResolvedValueOnce({ tags: [tag(1), tag(2)], untaggedSourceCount: 4 })
      .mockResolvedValueOnce({ tags: [tag(1), tag(2), tag(3)], untaggedSourceCount: 3 })
      .mockResolvedValueOnce({ tags: [tag(2, 'Renamed')], untaggedSourceCount: 5 })
    const { state } = setup()
    await flushPromises()
    await state.refreshTagFilterOptions()
    expect(state.availableSourceTags.value).toHaveLength(3)
    expect(state.untaggedSourceCount.value).toBe(3)
    window.dispatchEvent(new Event('focus'))
    await flushPromises()
    expect(state.availableSourceTags.value[0].name).toBe('Renamed')
    expect(state.untaggedSourceCount.value).toBe(5)
  })

  it('aborts on unmount and removes return-to-page listeners', async () => {
    const request = deferred()
    vi.mocked(listSourceTagFilterOptions).mockReturnValueOnce(request.promise)
    const { wrapper, state } = setup()
    const signal = vi.mocked(listSourceTagFilterOptions).mock.calls[0][0]?.signal
    wrapper.unmount()
    expect(signal?.aborted).toBe(true)
    request.resolve({ tags: [tag(1)], untaggedSourceCount: 9 })
    window.dispatchEvent(new Event('focus'))
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(state.availableSourceTags.value).toEqual([])
    expect(listSourceTagFilterOptions).toHaveBeenCalledTimes(1)
  })

  it('refreshes when the document becomes visible but not while hidden', async () => {
    vi.mocked(listSourceTagFilterOptions)
      .mockResolvedValueOnce({ tags: [tag(1), tag(2)], untaggedSourceCount: 1 })
      .mockResolvedValueOnce({ tags: [tag(1), tag(2)], untaggedSourceCount: 2 })
    const { state } = setup()
    await flushPromises()
    const visibility = vi.spyOn(document, 'visibilityState', 'get')
    try {
      visibility.mockReturnValue('hidden')
      document.dispatchEvent(new Event('visibilitychange'))
      await flushPromises()
      expect(listSourceTagFilterOptions).toHaveBeenCalledTimes(1)
      visibility.mockReturnValue('visible')
      document.dispatchEvent(new Event('visibilitychange'))
      await flushPromises()
      expect(state.untaggedSourceCount.value).toBe(2)
    } finally {
      visibility.mockRestore()
    }
  })

  it('preserves valid active selections and existing data when a refresh fails', async () => {
    vi.mocked(listSourceTagFilterOptions)
      .mockResolvedValueOnce({ tags: [tag(1), tag(2)], untaggedSourceCount: 4 })
      .mockRejectedValueOnce(new Error('Offline'))
    const { state, selected } = setup()
    await flushPromises()
    const originalSelection = selected.value
    await state.refreshTagFilterOptions()
    expect(selected.value).toBe(originalSelection)
    expect(state.availableSourceTags.value).toHaveLength(2)
    expect(state.untaggedSourceCount.value).toBe(4)
    expect(state.tagFilterError.value).toBe(true)
    expect(state.tagFilterLoading.value).toBe(false)
  })

  it('keeps the filter limit aligned with the API contract', () => {
    expect(SOURCE_TAG_FILTER_LIMIT).toBe(50)
  })

  it('accepts fifty draft tags, rejects the fifty-first, and only applies valid drafts', async () => {
    const tags = Array.from({ length: 51 }, (_, index) => tag(index + 1))
    vi.mocked(listSourceTagFilterOptions).mockResolvedValueOnce({ tags, untaggedSourceCount: 0 })
    const { state, selected, draft } = setup()
    expect(state.applyTagFilterIds()).toBe(false)
    await flushPromises()
    const fifty = tags.slice(0, 50).map((tag) => tag.id)
    state.setTagFilterDraftIds(fifty)
    state.setTagFilterDraftIds(tags.map((tag) => tag.id))
    expect(draft.value).toEqual(fifty)
    // Draft editing does not query or change the active selection until Apply.
    expect(selected.value).toEqual([1, 2])
    expect(state.applyTagFilterIds()).toBe(true)
    expect(selected.value).toEqual(fifty)
    draft.value = tags.map((tag) => tag.id)
    expect(state.applyTagFilterIds()).toBe(false)
    expect(selected.value).toEqual(fifty)
    expect(listSourceTagFilterOptions).toHaveBeenCalledTimes(1)
  })

  it('does not apply a draft after catalog loading fails', async () => {
    vi.mocked(listSourceTagFilterOptions).mockRejectedValueOnce(new Error('Offline'))
    const { state, selected } = setup()
    await flushPromises()
    state.setTagFilterDraftIds([1])
    expect(state.applyTagFilterIds()).toBe(false)
    expect(selected.value).toEqual([1, 2])
  })
})
