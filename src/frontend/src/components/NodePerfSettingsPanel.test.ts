// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { ElButton, ElInputNumber, ElMessage, ElOption, ElSelect } from 'element-plus'
import NodePerfSettingsPanel from './NodePerfSettingsPanel.vue'
import { en } from '../locales/en'
import zh from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import es from '../../../../language-packs/packs/es/frontend/messages.json'
import type { ApiNode } from '../types/node'

const mocks = vi.hoisted(() => ({
  updateNode: vi.fn(),
  getCache: vi.fn(),
  operateCache: vi.fn(),
}))

vi.mock('../lib/nodeApi', () => ({ updateNode: mocks.updateNode }))
vi.mock('../lib/nodeCacheApi', async (original) => ({ ...await original<object>(), getNodeCache: mocks.getCache, operateNodeCache: mocks.operateCache }))

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

function setup(target: ApiNode = node, hideActions = false, locale: 'en' | 'zh-hans' | 'es' = 'en') {
  const wrapper = mount(NodePerfSettingsPanel, {
    props: { node: target, hideActions },
    global: {
      plugins: [createI18n({ legacy: false, locale, messages: { en, 'zh-hans': zh, es } })],
      components: { ElInputNumber, ElSelect, ElOption, ElButton },
      directives: { 'table-overflow-title': {} },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}

function panelApi(wrapper: VueWrapper) {
  return wrapper.vm as unknown as {
    savePerfSettings: (target: ApiNode) => Promise<ApiNode | null>
    saveAllSettings: (target: ApiNode) => Promise<ApiNode | null>
    cancelPerfEdits: () => void
    resetPerfDefaults: () => void
    hasPerfChanges: boolean
    saveBlocked: boolean
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
  mocks.getCache.mockResolvedValue({ supported: false, policy: null, application_state: "unsupported", report: {} })
  mocks.operateCache.mockResolvedValue({ id: "cache-status", kind: "cache.status", status: "success" })
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
    expect(wrapper.find('.node-perf-quota-error').exists()).toBe(true)
    await expect(panelApi(wrapper).savePerfSettings(node)).rejects.toThrow('invalid resource quota')
    expect(mocks.updateNode).not.toHaveBeenCalled()
    expect(ElMessage.warning).toHaveBeenCalled()
  })

  it('disables inline saving while a quota is empty and allows correction', async () => {
    const wrapper = setup()
    await enterQuota(wrapper, 1, '')
    expect(wrapper.findAllComponents(ElButton).at(-1)?.props('disabled')).toBe(true)
    await enterQuota(wrapper, 1, '4096')
    expect(wrapper.find('.node-perf-quota-error').exists()).toBe(false)
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

  it('does not send the legacy cache value before capability confirmation', async () => {
    const legacy = { ...node, metadata: { ...node.metadata,
      perf_settings: { cpu_cores: 3, mem_cap_mb: 2050, kopia_cache_mb: 0 },
    } }
    const wrapper = setup(legacy)
    expect(wrapper.find('.node-perf-status').exists()).toBe(false)
    expect(wrapper.findAllComponents(ElInputNumber)[1].props('modelValue')).toBe(2050)
    expect(panelApi(wrapper).hasPerfChanges).toBe(true)
    await panelApi(wrapper).savePerfSettings(legacy)
    const metadata = mocks.updateNode.mock.calls[0][1].metadata
    expect(Object.keys(metadata)).toEqual(['perf_settings'])
    expect(metadata.perf_settings).toMatchObject({ cpuCores: 3, memCapMb: 2050 })
    expect(metadata.perf_settings).not.toHaveProperty('kopiaCacheMb')
  })

  it.each(['pending', 'applied', 'unsupported', 'monitoring', 'degraded'])('keeps configuration status out of the page without changing saved settings (%s)', async (state) => {
    const target = { ...node, metadata: { ...node.metadata,
      performance_policy: { revision: 2 },
      performance_status: { revision: state === 'pending' ? 1 : 2, reported_at: new Date().toISOString(), items: {
        cpu: { state, reason: 'Internal status detail' },
      } },
    } }
    const wrapper = setup(target)
    expect(wrapper.find('.node-perf-status').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('View application details')
    expect(wrapper.text()).not.toContain('Internal status detail')
    expect(wrapper.findAll('h4')[0].text()).toBe('Resource Quota')
    expect(wrapper.text()).toContain('Cache Management')
    expect(wrapper.text()).toContain('Log Settings')
    expect(target.metadata.performance_status.items.cpu.state).toBe(state)
  })

  it('waits for authoritative cache capability before showing legacy controls', () => {
    const wrapper = setup()
    expect(wrapper.text()).toContain('Checking cache capability…')
    expect(wrapper.text()).not.toContain('Cache per repository')
    expect(wrapper.text()).not.toContain('Kopia')
  })

  it('blocks saving a cleared auxiliary setting without replacing it with a default', async () => {
    const wrapper = setup()
    await enterQuota(wrapper, 3, '')
    await expect(panelApi(wrapper).savePerfSettings(node)).rejects.toThrow('invalid resource quota')
    expect(mocks.updateNode).not.toHaveBeenCalled()
    expect(wrapper.find('.node-perf-quota-error').text()).toContain('Retention Period')
  })
})

describe('new cache mode preserves legacy configuration without displaying a false budget', () => {
  it('hides legacy cache controls as soon as the node advertises the new capability', async () => {
    const target = { ...node, metadata: {
      ...node.metadata,
      inventory: { cpu_cores: 8, memory_total_bytes: 16 * 1024 ** 3, capabilities: ['cache_management_v1'] },
      performance_policy: { revision: 1 },
    } } as unknown as ApiNode
    const wrapper = setup(target)
    expect(wrapper.text()).toContain('Cache Management')
    expect(wrapper.text()).not.toContain('Kopia cache per repository')
  })

  it('does not show stale fixed-budget performance reports for a capable node', async () => {
    const target = { ...node, metadata: {
      ...node.metadata,
      inventory: { cpu_cores: 8, memory_total_bytes: 16 * 1024 ** 3, capabilities: ['cache_management_v1'] },
      cache_status: { mode: 'demand_driven', application_state: 'applied' },
      performance_policy: { revision: 1 },
      performance_status: { revision: 1, items: { cache: { state: 'pending', reason: 'Latest confirmed managed repository cache budget: 0 MB.' }, cpu: { state: 'applied', reason: 'CPU quota unchanged.' } } },
    } } as unknown as ApiNode
    const wrapper = setup(target)
    expect(wrapper.text()).not.toContain('Latest confirmed managed repository cache budget: 0 MB.')
    expect(wrapper.text()).not.toContain('Kopia cache per repository')
    expect(wrapper.text()).toContain('Cache Management')
    expect(wrapper.text()).not.toContain('CPU quota unchanged.')
  })
})


describe('footer save with binary cache drafts', () => {
  async function binarySetup(hideActions = true) {
    const target = { ...node, metadata: { ...node.metadata, performance_policy: { revision: 1 } } } as ApiNode
    mocks.getCache.mockResolvedValue({
      supported: true, policy_unit_supported: true, application_state: 'applied',
      policy: { schema_version: 1, revision: 1, blocking_bytes: 10 * 1024 ** 3, warning_bytes: 20 * 1024 ** 3 },
      report: {},
    })
    mocks.operateCache.mockImplementation(async (_id, body) => ({ id: body.operation, kind: 'cache.policy.apply', status: 'success' }))
    const wrapper = setup(target, hideActions)
    await flushPromises()
    mocks.operateCache.mockClear()
    return { wrapper, target }
  }
  it('notifies the outer drawer when only the cache threshold is saved', async () => {
    const { wrapper, target } = await binarySetup()
    await wrapper.get('#cache-blocking').setValue('12')
    expect(panelApi(wrapper).hasPerfChanges).toBe(true)
    expect(await panelApi(wrapper).saveAllSettings(target)).toBe(target)
    expect(mocks.updateNode).not.toHaveBeenCalled()
    expect(mocks.operateCache).toHaveBeenCalledWith(25, { operation: 'save_policy', blocking_bytes: 12 * 1024 ** 3 }, expect.any(AbortSignal))
  })
  it('shows Saving, blocks duplicate submissions, and reports one concise success without a waiting error', async () => {
    const { wrapper, target } = await binarySetup(false)
    await enterQuota(wrapper, 0, '3')
    await wrapper.get('#cache-blocking').setValue('12')
    let finish!: (task: { id: string; kind: string; status: string }) => void
    mocks.operateCache.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const save = panelApi(wrapper).saveAllSettings(target)
    await flushPromises()
    expect(panelApi(wrapper).saveBlocked).toBe(true)
    const button = wrapper.findAllComponents(ElButton).at(-1)!
    expect(button.props('loading')).toBe(true)
    expect(button.props('disabled')).toBe(true)
    expect(button.text()).toBe(en.nodesDetail.perfSaving)
    expect(wrapper.find('.node-cache-policy-body [role="alert"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain(en.nodeCache.waitForOperation)
    expect(await panelApi(wrapper).saveAllSettings(target)).toBeNull()
    expect(mocks.updateNode).toHaveBeenCalledTimes(1)
    expect(mocks.operateCache).toHaveBeenCalledTimes(1)
    finish({ id: 'applied', kind: 'cache.policy.apply', status: 'success' })
    await save
    await flushPromises()
    expect(ElMessage.success).toHaveBeenCalledExactlyOnceWith({ message: 'Settings saved.', grouping: true, duration: 3000 })
    expect(ElMessage.error).not.toHaveBeenCalled()
    expect(panelApi(wrapper).saveBlocked).toBe(false)
    expect(button.props('loading')).toBe(false)
  })
  it('disables saving during a background refresh and restores it without an error', async () => {
    const { wrapper, target } = await binarySetup(false)
    await wrapper.get('#cache-blocking').setValue('12')
    let finish!: (task: { id: string; kind: string; status: string }) => void
    mocks.operateCache.mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const refresh = wrapper.findAllComponents(ElButton).find(button => button.attributes('aria-label') === en.nodeCache.refresh)!
    await refresh.trigger('click')
    await flushPromises()
    expect(panelApi(wrapper).saveBlocked).toBe(true)
    expect(wrapper.findAllComponents(ElButton).at(-1)!.props('disabled')).toBe(true)
    expect(wrapper.get('.node-cache-policy-body [role="status"]').text()).toBe(en.nodeCache.refreshingStatus)
    expect(await panelApi(wrapper).saveAllSettings(target)).toBeNull()
    expect(ElMessage.warning).not.toHaveBeenCalled()
    expect(ElMessage.error).not.toHaveBeenCalled()
    finish({ id: 'refreshed', kind: 'cache.status', status: 'success' })
    await flushPromises()
    expect(panelApi(wrapper).saveBlocked).toBe(false)
    expect(wrapper.findAllComponents(ElButton).at(-1)!.props('disabled')).toBe(false)
  })
  it('keeps the threshold draft and explains partial saves instead of claiming complete success', async () => {
    const { wrapper, target } = await binarySetup()
    await enterQuota(wrapper, 0, '3')
    await wrapper.get('#cache-blocking').setValue('12')
    mocks.operateCache.mockResolvedValueOnce({ id: 'failed', kind: 'cache.policy.apply', status: 'failed', error: 'Cache policy delivery failed.' })
    await expect(panelApi(wrapper).saveAllSettings(target)).rejects.toThrow('Cache policy delivery failed.')
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.error).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining('Resource and log settings were saved, but the cache threshold could not be saved:') }))
    expect(wrapper.get<HTMLInputElement>('#cache-blocking').element.value).toBe('12')
    expect(panelApi(wrapper).hasPerfChanges).toBe(true)
    mocks.operateCache.mockClear()
    await panelApi(wrapper).saveAllSettings(target)
    expect(mocks.updateNode).toHaveBeenCalledTimes(1) // Retry only the unsuccessful cache change.
    expect(mocks.operateCache).toHaveBeenCalledTimes(1)
    expect(ElMessage.success).toHaveBeenCalledTimes(1)
  })
  it('does not claim settings were saved when the resource request fails', async () => {
    const { wrapper, target } = await binarySetup()
    await enterQuota(wrapper, 0, '3')
    mocks.updateNode.mockRejectedValueOnce(new Error('Network unavailable'))
    await expect(panelApi(wrapper).saveAllSettings(target)).rejects.toThrow()
    expect(mocks.operateCache).not.toHaveBeenCalled()
    expect(ElMessage.success).not.toHaveBeenCalled()
    expect(ElMessage.error).toHaveBeenCalledTimes(1)
    expect(panelApi(wrapper).hasPerfChanges).toBe(true)
  })
  it('does not send success notifications for an unchanged form', async () => {
    const { wrapper, target } = await binarySetup()
    expect(await panelApi(wrapper).saveAllSettings(target)).toBeNull()
    expect(ElMessage.success).not.toHaveBeenCalled()
  })
  it('validates a cache draft before the performance endpoint and restores both on cancel', async () => {
    const { wrapper, target } = await binarySetup()
    await enterQuota(wrapper, 0, '3')
    await wrapper.get('#cache-blocking').setValue('')
    await expect(panelApi(wrapper).saveAllSettings(target)).rejects.toThrow('GiB')
    expect(mocks.updateNode).not.toHaveBeenCalled()
    expect(mocks.operateCache).not.toHaveBeenCalled()
    panelApi(wrapper).cancelPerfEdits()
    await flushPromises()
    expect(wrapper.get<HTMLInputElement>('#cache-blocking').element.value).toBe('10')
    expect(panelApi(wrapper).hasPerfChanges).toBe(false)
  })
})


describe('localized advanced-settings save feedback', () => {
  it.each([
    ['en', 'agent', en.nodesDetail.perfSaveSuccess], ['zh-hans', 'agent', zh.nodesDetail.perfSaveSuccess], ['es', 'agent', es.nodesDetail.perfSaveSuccess],
    ['en', 'proxy', en.nodesDetail.perfSaveSuccess], ['zh-hans', 'proxy', zh.nodesDetail.perfSaveSuccess], ['es', 'proxy', es.nodesDetail.perfSaveSuccess],
  ] as const)('uses concise feedback in %s for %s settings', async (locale, role, message) => {
    const target = { ...node, role } as ApiNode
    const wrapper = setup(target, true, locale)
    await flushPromises()
    await enterQuota(wrapper, 0, '3')
    await panelApi(wrapper).saveAllSettings(target)
    expect(ElMessage.success).toHaveBeenCalledExactlyOnceWith({ message, grouping: true, duration: 3000 })
  })
})
