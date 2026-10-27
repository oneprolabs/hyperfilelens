// @vitest-environment jsdom

import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus, { ElDatePicker, ElDrawer } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Repositories from './node/Repositories.vue'
import FlowBackupSourceDetailDrawer from './protection/components/FlowBackupSourceDetailDrawer.vue'

vi.mock('vue-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('vue-router')>()),
  useRoute: () => ({ path: '/test', query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  onBeforeRouteLeave: vi.fn(),
}))

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { get: vi.fn().mockResolvedValue({ results: [], count: 0 }) },
}))

type DateRange = [Date, Date] | null
type FilterState = {
  mode: string
  range: DateRange
  draft: { dateRange: DateRange }
  count: number
  open: () => void
  changeMode: (value: string) => void
  apply: () => void
  reset: () => void
  closed: () => void
  params: () => { created_after?: string; created_before?: string }
}

const wrappers: VueWrapper[] = []
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 9, 27, 12, 30))
  vi.stubGlobal('ResizeObserver', class {
    observe = vi.fn()
    unobserve = vi.fn()
    disconnect = vi.fn()
  })
})
afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

const cases = [
  {
    name: 'repository',
    component: Repositories,
    props: {},
    keys: ['repositoryTaskTimeMode', 'repositoryTaskDateRange', 'repositoryTaskAdvancedFilterDraft',
      'repositoryTaskAdvancedFilterCount', 'openRepositoryTaskAdvancedFilter', 'onRepositoryTaskTimeModeChange',
      'applyRepositoryTaskAdvancedFilters', 'resetRepositoryTaskAdvancedFilterDraft',
      'onRepositoryTaskAdvancedFilterClosed', 'repositoryTaskCreatedRangeParams'],
  },
  {
    name: 'backup source',
    component: FlowBackupSourceDetailDrawer,
    props: { modelValue: false, source: null, backupFlowTasks: [], restoreFlowTasks: [] },
    keys: ['taskFilterTimeMode', 'taskFilterDateRange', 'taskAdvancedFilterDraft',
      'sourceTaskAdvancedFilterCount', 'openSourceTaskAdvancedFilterDrawer', 'onSourceTaskTimeModeChange',
      'applySourceTaskAdvancedFilters', 'resetSourceTaskAdvancedFilterDraft',
      'onSourceTaskAdvancedFilterClosed', 'sourceTaskCreatedRangeParams'],
  },
] as const

function mountFilter(testCase: typeof cases[number]) {
  const wrapper = mount(testCase.component, {
    props: testCase.props,
    global: {
      plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: {}, missingWarn: false, fallbackWarn: false })],
      stubs: {
        ModulePage: { template: '<div><slot /></div>' },
        ElDrawer: false,
        ElDatePicker: false,
        ElForm: { template: '<div><slot /></div>' },
        ElFormItem: { template: '<div><slot /></div>' },
        ElTable: true,
        ElPagination: true,
      },
      config: { warnHandler: () => {} },
    },
  })
  wrappers.push(wrapper)
  const setup = (wrapper.vm.$ as unknown as { setupState: Record<string, unknown> }).setupState
  const state = {} as FilterState
  const aliases = ['mode', 'range', 'draft', 'count', 'open', 'changeMode', 'apply', 'reset', 'closed', 'params']
  testCase.keys.forEach((key, index) => Object.defineProperty(state, aliases[index]!, {
    get: () => setup[key],
    set: (value: unknown) => { setup[key] = value },
  }))
  return { wrapper, state }
}

describe.each(cases)('$name advanced-filter behavior', (testCase) => {
  it('keeps quick-range request parameters unchanged until Apply', async () => {
    const { wrapper, state } = mountFilter(testCase)
    state.changeMode('7d')
    const before = state.params()
    state.changeMode('range')
    await flushPromises()
    expect(state.mode).toBe('7d')
    expect(state.params()).toEqual(before)
    const picker = wrapper.findComponent(ElDatePicker)
    expect(picker.exists()).toBe(true)
    const selected = [new Date(2026, 9, 20, 9), new Date(2026, 9, 21, 18, 30)]
    picker.vm.$emit('update:modelValue', selected)
    await flushPromises()
    expect(state.mode).toBe('7d')
    expect(state.count).toBe(0)
    state.apply()
    await flushPromises()
    expect(state.mode).toBe('range')
    expect(state.params()).toEqual({
      created_after: selected[0]!.toISOString(),
      created_before: new Date(2026, 9, 21, 18, 30, 0, 999).toISOString(),
    })
    expect(state.count).toBe(1)
    state.changeMode('24h')
    expect(state.mode).toBe('24h')
    expect(state.range).toBeNull()
    expect(state.count).toBe(0)
    expect(state.params()).toEqual({
      created_after: new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString(),
      created_before: undefined,
    })
  })

  it('clamps today end-of-day to now without changing the picker-owned dates', async () => {
    const { wrapper, state } = mountFilter(testCase)
    state.open()
    await flushPromises()
    const start = new Date(2026, 9, 20)
    const end = new Date(2026, 9, 27, 23, 59, 59)
    wrapper.findComponent(ElDatePicker).vm.$emit('update:modelValue', [start, end])
    state.apply()
    expect(state.range).toEqual([start, new Date()])
    expect(state.params()).toEqual({
      created_after: start.toISOString(),
      created_before: new Date(2026, 9, 27, 12, 30, 0, 999).toISOString(),
    })
    expect(end.getHours()).toBe(23)
    expect(start.getMilliseconds()).toBe(0)
  })

  it('restores applied dates in the same drawer and discards edits or draft resets', async () => {
    const { wrapper, state } = mountFilter(testCase)
    state.open()
    await flushPromises()
    const picker = wrapper.findComponent(ElDatePicker)
    const selected = [new Date(2026, 9, 20, 9), new Date(2026, 9, 21, 18, 30)]
    picker.vm.$emit('update:modelValue', selected)
    state.apply()
    await flushPromises()
    const applied = state.params()
    state.open()
    await flushPromises()
    expect(wrapper.findComponent(ElDatePicker).props('modelValue')).toEqual(selected)
    expect(wrapper.findComponent(ElDatePicker).findAll('input.el-range-input').map((input) => (input.element as HTMLInputElement).value))
      .toEqual(['2026-10-20 09:00:00', '2026-10-21 18:30:00'])
    wrapper.findComponent(ElDatePicker).vm.$emit('update:modelValue', [new Date(2026, 9, 22), new Date(2026, 9, 23)])
    wrapper.findAllComponents(ElDrawer).at(-1)!.vm.$emit('update:modelValue', false)
    state.closed()
    state.open()
    await flushPromises()
    expect(state.params()).toEqual(applied)
    expect(wrapper.findComponent(ElDatePicker).props('modelValue')).toEqual(selected)
    state.reset()
    expect(state.params()).toEqual(applied)
    state.closed()
    state.open()
    await flushPromises()
    expect(wrapper.findComponent(ElDatePicker).props('modelValue')).toEqual(selected)
    state.reset()
    state.apply()
    state.open()
    await flushPromises()
    expect(state.range).toBeNull()
    expect(state.count).toBe(0)
    expect(state.params()).toEqual({ created_after: undefined, created_before: undefined })
    expect(wrapper.findComponent(ElDatePicker).findAll('input.el-range-input').map((input) => (input.element as HTMLInputElement).value))
      .toEqual(['', ''])
  })
})
