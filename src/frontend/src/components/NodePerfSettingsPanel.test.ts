// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { ElButton, ElInputNumber, ElMessage, ElOption, ElSelect } from 'element-plus'
import NodePerfSettingsPanel from './NodePerfSettingsPanel.vue'
import { en } from '../locales/en'
import type { ApiNode } from '../types/node'

const mocks = vi.hoisted(() => ({
  updateNode: vi.fn(),
}))

vi.mock('../lib/nodeApi', () => ({ updateNode: mocks.updateNode }))

const node = {
  id: 25,
  name: 'Host',
  role: 'agent',
  metadata: {
    inventory: { cpu_cores: 8, memory_total_bytes: 16 * 1024 ** 3 },
    unrelated: 'preserved',
  },
} as unknown as ApiNode

const wrappers: VueWrapper[] = []

function setup(target: ApiNode = node, hideActions = false) {
  const wrapper = mount(NodePerfSettingsPanel, {
    props: { node: target, hideActions },
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      components: { ElInputNumber, ElSelect, ElOption, ElButton },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}

function panelApi(wrapper: VueWrapper) {
  return wrapper.vm as unknown as {
    savePerfSettings: (target: ApiNode) => Promise<ApiNode | null>
    cancelPerfEdits: () => void
    resetPerfDefaults: () => void
    hasPerfChanges: boolean
  }
}

async function enterQuota(wrapper: VueWrapper, index: number, value: string) {
  const input = wrapper.findAllComponents(ElInputNumber)[index].get('input')
  await input.setValue(value)
  await input.trigger('change')
  await input.trigger('blur')
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.spyOn(ElMessage, 'warning').mockImplementation(() => ({ close: vi.fn() }))
  vi.spyOn(ElMessage, 'success').mockImplementation(() => ({ close: vi.fn() }))
  vi.spyOn(ElMessage, 'error').mockImplementation(() => ({ close: vi.fn() }))
  mocks.updateNode.mockResolvedValue(node)
})

afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.restoreAllMocks()
})

describe('node quota precision', () => {
  it('uses bandwidth-style integer inputs with exact values and units, without sliders', () => {
    const wrapper = setup()
    const inputs = wrapper.findAllComponents(ElInputNumber)
    expect(inputs[0].props()).toMatchObject({
      modelValue: 2, min: 1, max: 8, step: 1, precision: 0, controls: false,
    })
    expect(inputs[1].props()).toMatchObject({
      modelValue: 2048, min: 128, max: 16384, step: 1, precision: 0, controls: false,
    })
    expect(inputs[2].props('controls')).toBe(false)
    const units = wrapper.findAll('.hfl-detail-form-input__suffix').map(item => item.text())
    expect(units.slice(0, 3)).toEqual(['Cores', 'MB', 'MB/s'])
    expect(wrapper.find('.el-slider').exists()).toBe(false)
    expect(wrapper.text()).toContain('25% (2 / 8 Cores)')
    expect(wrapper.text()).toContain('13% (2 GB / 16 GB)')
  })

  it.each([2048, 4096, 2050])('saves exact memory %i MB without step snapping', async (memory) => {
    const wrapper = setup()
    await enterQuota(wrapper, 0, '3')
    await enterQuota(wrapper, 1, String(memory))
    await panelApi(wrapper).savePerfSettings(node)
    expect(mocks.updateNode).toHaveBeenCalledWith(25, {
      metadata: {
        perf_settings: expect.objectContaining({ cpuCores: 3, memCapMb: memory }),
      },
    }, 'tenant')
  })

  it.each([0, 1])('does not save or coerce an empty quota, including drawer saves (%i)', async (index) => {
    const wrapper = setup(node, true)
    await enterQuota(wrapper, index, '')
    expect(panelApi(wrapper).hasPerfChanges).toBe(true)
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)
    await expect(panelApi(wrapper).savePerfSettings(node)).rejects.toThrow('invalid resource quota')
    expect(mocks.updateNode).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalled()
  })

  it('disables inline saving while a quota is empty and allows correction', async () => {
    const wrapper = setup()
    await enterQuota(wrapper, 1, '')
    expect(wrapper.findAllComponents(ElButton).at(-1)?.props('disabled')).toBe(true)
    await enterQuota(wrapper, 1, '4096')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.findAllComponents(ElButton).at(-1)?.props('disabled')).toBe(false)
    await panelApi(wrapper).savePerfSettings(node)
    expect(mocks.updateNode).toHaveBeenCalledOnce()
  })

  it('retains integer input precision and host bounds', async () => {
    const wrapper = setup()
    await enterQuota(wrapper, 0, '99')
    await enterQuota(wrapper, 1, '1')
    expect(wrapper.findAllComponents(ElInputNumber)[0].props('modelValue')).toBe(8)
    expect(wrapper.findAllComponents(ElInputNumber)[1].props('modelValue')).toBe(128)
    await enterQuota(wrapper, 0, '2.4')
    expect(wrapper.findAllComponents(ElInputNumber)[0].props('modelValue')).toBe(2)
    await enterQuota(wrapper, 1, '99999')
    expect(wrapper.findAllComponents(ElInputNumber)[1].props('modelValue')).toBe(16384)
  })

  it('preserves loaded exact values and supports cancel and reset defaults', async () => {
    const savedNode = {
      ...node,
      metadata: { ...node.metadata, perf_settings: { cpuCores: 3, memCapMb: 2050 }, performance_policy: { revision: 1 } },
    }
    const wrapper = setup(savedNode)
    const api = panelApi(wrapper)
    expect(api.hasPerfChanges).toBe(false)
    await enterQuota(wrapper, 1, '')
    api.cancelPerfEdits()
    await nextTick()
    expect(wrapper.findAllComponents(ElInputNumber)[1].props('modelValue')).toBe(2050)
    expect(api.hasPerfChanges).toBe(false)
    api.resetPerfDefaults()
    await nextTick()
    expect(wrapper.findAllComponents(ElInputNumber)[1].props('modelValue')).toBe(2048)
    expect(api.hasPerfChanges).toBe(true)
  })

  it('does not replace cleared quotas when host inventory changes', async () => {
    const wrapper = setup()
    await enterQuota(wrapper, 1, '')
    await wrapper.setProps({
      node: {
        ...node,
        metadata: {
          ...node.metadata,
          inventory: { cpu_cores: 4, memory_total_bytes: 8 * 1024 ** 3 },
        },
      },
    })
    expect(wrapper.findAllComponents(ElInputNumber)[1].get('input').element.value).toBe('')
    await expect(panelApi(wrapper).savePerfSettings(node)).rejects.toThrow('invalid resource quota')
    expect(mocks.updateNode).not.toHaveBeenCalled()
  })

  it('requires an explicit save to activate legacy settings and sends only settings metadata', async () => {
    const legacy = { ...node, metadata: { ...node.metadata,
      perf_settings: { cpu_cores: 3, mem_cap_mb: 2050, kopia_cache_mb: 0 },
    } }
    const wrapper = setup(legacy)
    expect(wrapper.text()).toContain('not activated automatically')
    expect(wrapper.findAllComponents(ElInputNumber)[1].props('modelValue')).toBe(2050)
    expect(panelApi(wrapper).hasPerfChanges).toBe(true)
    await panelApi(wrapper).savePerfSettings(legacy)
    const metadata = mocks.updateNode.mock.calls[0][1].metadata
    expect(Object.keys(metadata)).toEqual(['perf_settings'])
    expect(metadata.perf_settings).toMatchObject({ cpuCores: 3, memCapMb: 2050, kopiaCacheMb: 0 })
  })

  it('separates waiting, applied, unsupported and soft monitoring status', async () => {
    const wrapper = setup({ ...node, metadata: { ...node.metadata,
      performance_policy: { revision: 2 },
    } })
    expect(wrapper.text()).toContain('Waiting for Agent application')
    await wrapper.setProps({ node: { ...node, metadata: {
      ...node.metadata, performance_policy: { revision: 2 },
      performance_status: { revision: 2, reported_at: new Date().toISOString(), items: {
        cpu: { state: 'unsupported', reason: 'No controller' },
        memory: { state: 'monitoring', reason: 'Soft budget' },
        logLevel: { state: 'applied', reason: 'Runtime logs' },
      } },
    } } })
    expect(wrapper.text()).toContain('Not supported')
    expect(wrapper.text()).toContain('Soft budget monitoring')
    expect(wrapper.text()).toContain('Applied')
    expect(wrapper.text()).not.toContain('Waiting for Agent application')
  })

  it('blocks saving cleared auxiliary settings without replacing them with defaults', async () => {
    const wrapper = setup()
    await enterQuota(wrapper, 3, '')
    await expect(panelApi(wrapper).savePerfSettings(node)).rejects.toThrow('invalid resource quota')
    expect(mocks.updateNode).not.toHaveBeenCalled()
    expect(wrapper.find('[role="alert"]').text()).toContain('Kopia cache')
  })
})
