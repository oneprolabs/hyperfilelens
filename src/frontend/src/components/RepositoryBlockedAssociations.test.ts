// @vitest-environment jsdom
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../locales/en'
import RepositoryBlockedAssociations from './RepositoryBlockedAssociations.vue'

const { list } = vi.hoisted(() => ({ list: vi.fn() }))
vi.mock('../lib/storageRepositoryApi', () => ({ listStorageRepositoryAssociatedSources: list }))

function deferred() {
  let resolve!: (value: unknown) => void
  const promise = new Promise(done => { resolve = done })
  return { promise, resolve }
}
function row(id: number) {
  return { backup_config_id: id, backup_config_name: `Configuration ${id}`, source_name: `Source ${id}` }
}
function mountDetails() {
  return mount(RepositoryBlockedAssociations, {
    props: { repositoryId: 7 },
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      directives: { loading: (element, binding) => { element.dataset.loading = String(binding.value) } },
      stubs: {
        ElButton: defineComponent({ emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' }),
        HflPagination: defineComponent({
          props: ['currentPage', 'total'], emits: ['update:currentPage'],
          template: '<div><button data-test="first" @click="$emit(\'update:currentPage\', 1)">1</button><button data-test="next" @click="$emit(\'update:currentPage\', 2)">2</button></div>',
        }),
      },
    },
  })
}
const wrappers: ReturnType<typeof mountDetails>[] = []
function render() { const wrapper = mountDetails(); wrappers.push(wrapper); return wrapper }
beforeEach(() => list.mockReset())
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))

describe('Repository blocked associations disclosure', () => {
  it('lets multiple repository details expand independently', async () => {
    list.mockResolvedValue({ count: 1, results: [row(1)] })
    const first = render()
    const second = render()
    await second.setProps({ repositoryId: 8 })
    await first.get('.repository-blocked-associations__toggle').trigger('click')
    await flushPromises()
    expect(second.get('.repository-blocked-associations__toggle').attributes('aria-expanded')).toBe('false')
    await second.get('.repository-blocked-associations__toggle').trigger('click')
    await flushPromises()
    expect(first.get('.repository-blocked-associations__toggle').attributes('aria-expanded')).toBe('true')
    expect(second.get('.repository-blocked-associations__toggle').attributes('aria-expanded')).toBe('true')
    expect(list.mock.calls.map(call => call[0])).toEqual([7, 8])
  })
  it('starts collapsed, loads on first expansion, keeps loading visible and caches pages', async () => {
    const request = deferred()
    list.mockReturnValueOnce(request.promise).mockResolvedValueOnce({ count: 25, results: [row(11)] })
    const wrapper = render()
    expect(wrapper.get('.repository-blocked-associations__toggle-label').text()).toBe('View associated backup configurations')
    expect(list).not.toHaveBeenCalled()
    expect(wrapper.get('.repository-blocked-associations__toggle').attributes('aria-expanded')).toBe('false')
    await wrapper.get('.repository-blocked-associations__toggle').trigger('click')
    expect(list).toHaveBeenCalledWith(7, { page: 1, page_size: 10 }, { signal: expect.any(AbortSignal) })
    expect(wrapper.get('.repository-blocked-associations__body').attributes('data-loading')).toBe('true')
    request.resolve({ count: 25, results: [row(1)] })
    await flushPromises()
    expect(wrapper.get('.repository-blocked-associations__toggle-label').text()).toBe('View associated backup configurations (25)')
    expect(wrapper.text()).toContain('Configuration 1')
    expect(wrapper.text()).toContain('Source: Source 1')
    expect(wrapper.get('.repository-blocked-associations__body').attributes('data-loading')).toBe('false')
    await wrapper.get('.repository-blocked-associations__toggle').trigger('click')
    await wrapper.get('.repository-blocked-associations__toggle').trigger('click')
    expect(list).toHaveBeenCalledTimes(1)
    await wrapper.get('[data-test="next"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Configuration 11')
    await wrapper.get('[data-test="first"]').trigger('click')
    expect(wrapper.text()).toContain('Configuration 1')
    expect(list).toHaveBeenCalledTimes(2)
  })

  it('supports independent error retry and an empty result', async () => {
    list.mockRejectedValueOnce(new Error('unavailable')).mockResolvedValueOnce({ count: 0, results: [] })
    const wrapper = render()
    await wrapper.get('.repository-blocked-associations__toggle').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    await wrapper.get('[role="alert"] button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('No associated backup configurations remain')
    expect(wrapper.find('[data-test="next"]').exists()).toBe(false)
  })

  it('ignores stale page results when the user returns to a cached page', async () => {
    const second = deferred()
    list.mockResolvedValueOnce({ count: 25, results: [row(1)] }).mockReturnValueOnce(second.promise)
    const wrapper = render()
    await wrapper.get('.repository-blocked-associations__toggle').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="next"]').trigger('click')
    await wrapper.get('[data-test="first"]').trigger('click')
    second.resolve({ count: 25, results: [row(11)] })
    await flushPromises()
    expect(wrapper.findAll('li').map(item => item.text())).toEqual(['Configuration 1Source: Source 1'])
  })

  it('aborts requests on close and a fresh disclosure starts collapsed without stale cached data', async () => {
    const request = deferred()
    list.mockReturnValue(request.promise)
    const wrapper = render()
    await wrapper.get('.repository-blocked-associations__toggle').trigger('click')
    const signal = list.mock.calls[0][2].signal as AbortSignal
    wrapper.unmount()
    expect(signal.aborted).toBe(true)
    request.resolve({ count: 25, results: [row(1)] })
    await flushPromises()
    const fresh = render()
    expect(fresh.get('.repository-blocked-associations__toggle').attributes('aria-expanded')).toBe('false')
    expect(fresh.text()).not.toContain('Configuration 1')
    expect(list).toHaveBeenCalledTimes(1)
  })
})
