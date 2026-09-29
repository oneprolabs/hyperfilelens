// @vitest-environment jsdom

import { flushPromises, shallowMount } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchLensHealth: vi.fn(),
  listLensModels: vi.fn(),
  patchLensModel: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('vue-router', async (importOriginal) => ({
  ...await importOriginal<typeof import('vue-router')>(),
  useRoute: () => ({ path: '/platform-ops/engine/ai-settings' }),
  useRouter: () => ({ push: vi.fn() }),
}))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error },
}))
vi.mock('../../lib/api', () => ({
  apiErrorMessage: () => 'request failed',
  apiErrorMessageI18n: () => 'connection failed',
}))
vi.mock('../../composables/useListTableLayout', () => ({
  useListTableLayout: () => ({
    tableMaxHeight: 400,
    layoutTable: vi.fn(),
    handleTableScroll: vi.fn(),
  }),
}))
vi.mock('../../lib/lensApi', () => ({
  fetchLensHealth: mocks.fetchLensHealth,
  listLensModels: mocks.listLensModels,
  patchLensModel: mocks.patchLensModel,
}))

import InsightAiSettings from './InsightAiSettings.vue'

const disabledModel = { uuid: 'model-1', is_active: false, name: 'Model 1' }
const otherModel = { uuid: 'model-2', is_active: false, name: 'Model 2' }
const tableStubs = {
  'el-table': { template: '<div><slot /></div>' },
  'el-table-column': { template: '<div />' },
  'el-empty': true,
  ElButton: {
    props: ['disabled'],
    template: '<button :disabled="disabled"><slot /></button>',
  },
  ElDropdown: { template: '<div><slot /></div>' },
  ElDropdownMenu: true,
  ElDropdownItem: true,
  ElInput: true,
  ElTag: {
    props: ['type', 'size'],
    template: '<span v-bind="$attrs" :data-type="type"><slot /></span>',
  },
}
const global = {
  stubs: tableStubs,
  directives: {
    loading: {},
    'table-overflow-title': {},
  },
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

describe('AI Models enable progress', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchLensHealth.mockResolvedValue({ lens: { configured: true, authenticated: true } })
    mocks.listLensModels.mockResolvedValue([disabledModel, otherModel])
  })

  it('marks only the requested model as testing until enable and refresh complete', async () => {
    const request = deferred<unknown>()
    const refresh = deferred<typeof disabledModel[]>()
    mocks.patchLensModel.mockReturnValueOnce(request.promise)
    mocks.listLensModels
      .mockResolvedValueOnce([disabledModel, otherModel])
      .mockReturnValueOnce(refresh.promise)
    const wrapper = shallowMount(InsightAiSettings, { global })
    await flushPromises()
    const state = wrapper.vm.$.setupState
    state.onSelectionChange([disabledModel])

    const enable = state.setActive(disabledModel, true)
    await nextTick()
    expect(state.testingModelUuids.has('model-1')).toBe(true)
    expect(state.testingModelUuids.has('model-2')).toBe(false)
    expect(state.selectedTesting).toBe(true)
    expect(mocks.success).not.toHaveBeenCalled()
    expect(mocks.patchLensModel).toHaveBeenCalledWith('model-1', { is_active: true })
    const moreActions = wrapper.findAll('button').find((button) => (
      button.text().includes('platformOps.engineActions.modelActions')
    ))
    expect(moreActions?.attributes('disabled')).toBeDefined()

    state.onSelectionChange([otherModel])
    await nextTick()
    expect(state.selectedTesting).toBe(false)
    expect(moreActions?.attributes('disabled')).toBeUndefined()
    state.onSelectionChange([disabledModel])
    request.resolve({})
    await flushPromises()
    expect(state.testingModelUuids.has('model-1')).toBe(true)
    expect(state.loading).toBe(false)
    refresh.resolve([{ ...disabledModel, is_active: true }, otherModel])
    await enable
    expect(state.testingModelUuids.has('model-1')).toBe(false)
    expect(state.singleSelected.is_active).toBe(true)
    expect(mocks.success).toHaveBeenCalledWith({
      message: 'insight.aiSettings.modelEnabled',
      grouping: true,
    })
    wrapper.unmount()
  })

  it('keeps the same status tag while switching from testing to enabled', async () => {
    const request = deferred<unknown>()
    const refresh = deferred<typeof disabledModel[]>()
    const renderedModel = reactive({ ...disabledModel })
    mocks.patchLensModel.mockReturnValueOnce(request.promise)
    mocks.listLensModels
      .mockResolvedValueOnce([disabledModel, otherModel])
      .mockReturnValueOnce(refresh.promise)
    const wrapper = shallowMount(InsightAiSettings, {
      global: {
        ...global,
        stubs: {
          ...tableStubs,
          'el-table-column': {
            props: ['className'],
            data: () => ({ model: renderedModel }),
            template: '<div :data-column-class="className"><slot :row="model" /></div>',
          },
        },
      },
    })
    await flushPromises()

    const enable = wrapper.vm.$.setupState.setActive(disabledModel, true)
    await nextTick()
    const status = wrapper.get('.insight-ai-models-status__tag')
    expect(status.attributes('data-type')).toBe('info')
    const slot = wrapper.get('.insight-ai-models-status')
    expect(slot.element.contains(status.element)).toBe(true)
    const content = status.get('.insight-ai-models-status__content')
    expect(content.find('.insight-ai-models-status__icon').exists()).toBe(true)
    expect(content.text()).toBe('insight.aiSettings.statusTesting')
    expect(status.element.closest('[data-column-class="hfl-table-no-tooltip"]')).not.toBeNull()

    request.resolve({})
    await flushPromises()
    expect(status.attributes('data-type')).toBe('info')
    expect(status.text()).toBe('insight.aiSettings.statusTesting')
    refresh.resolve([{ ...disabledModel, is_active: true }, otherModel])
    await enable
    renderedModel.is_active = true
    await nextTick()
    expect(wrapper.get('.insight-ai-models-status').element).toBe(slot.element)
    expect(wrapper.get('.insight-ai-models-status__tag').element).toBe(status.element)
    expect(status.attributes('data-type')).toBe('success')
    expect(status.text()).toBe('insight.aiSettings.statusActive')
    expect(status.find('.insight-ai-models-status__icon').exists()).toBe(false)
    wrapper.unmount()
  })

  it('allows another model to be enabled without retrying the one already testing', async () => {
    const first = deferred<unknown>()
    const second = deferred<unknown>()
    mocks.patchLensModel.mockImplementation((uuid: string) => (
      uuid === 'model-1' ? first.promise : second.promise
    ))
    const wrapper = shallowMount(InsightAiSettings, { global })
    await flushPromises()
    const state = wrapper.vm.$.setupState

    const firstEnable = state.setActive(disabledModel, true)
    await state.setActive(disabledModel, true)
    const secondEnable = state.setActive(otherModel, true)
    expect(mocks.patchLensModel).toHaveBeenCalledTimes(2)
    expect(state.testingModelUuids.has('model-1')).toBe(true)
    expect(state.testingModelUuids.has('model-2')).toBe(true)

    second.resolve({})
    await secondEnable
    expect(state.testingModelUuids.has('model-2')).toBe(false)
    expect(state.testingModelUuids.has('model-1')).toBe(true)
    first.resolve({})
    await firstEnable
    expect(state.testingModelUuids.size).toBe(0)
    wrapper.unmount()
  })

  it('does not let an older status refresh undo a concurrent enable', async () => {
    const firstPatch = deferred<unknown>()
    const secondPatch = deferred<unknown>()
    const firstRefresh = deferred<typeof disabledModel[]>()
    mocks.patchLensModel.mockImplementation((uuid: string) => (
      uuid === 'model-1' ? firstPatch.promise : secondPatch.promise
    ))
    mocks.listLensModels
      .mockResolvedValueOnce([disabledModel, otherModel])
      .mockReturnValueOnce(firstRefresh.promise)
      .mockResolvedValueOnce([
        { ...disabledModel, is_active: true },
        { ...otherModel, is_active: true },
      ])
    const wrapper = shallowMount(InsightAiSettings, { global })
    await flushPromises()
    const state = wrapper.vm.$.setupState

    const firstEnable = state.setActive(disabledModel, true)
    const secondEnable = state.setActive(otherModel, true)
    firstPatch.resolve({})
    await flushPromises()
    expect(mocks.listLensModels).toHaveBeenCalledTimes(2)

    secondPatch.resolve({})
    await flushPromises()
    expect(mocks.listLensModels).toHaveBeenCalledTimes(2)
    firstRefresh.resolve([{ ...disabledModel, is_active: true }, otherModel])
    await Promise.all([firstEnable, secondEnable])

    expect(mocks.listLensModels).toHaveBeenCalledTimes(3)
    expect(state.models.map((row: typeof disabledModel) => row.is_active)).toEqual([true, true])
    wrapper.unmount()
  })

  it('clears testing and leaves the model disabled when connection testing fails', async () => {
    mocks.patchLensModel.mockRejectedValueOnce({
      status: 400,
      errorCode: 'AI_MODEL.CONNECTION_TEST_FAILED',
      message: 'Connection test failed',
    })
    const wrapper = shallowMount(InsightAiSettings, { global })
    await flushPromises()
    const state = wrapper.vm.$.setupState

    await state.setActive(disabledModel, true)
    expect(state.testingModelUuids.has('model-1')).toBe(false)
    expect(state.models[0].is_active).toBe(false)
    expect(mocks.success).not.toHaveBeenCalled()
    expect(mocks.error).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
})
