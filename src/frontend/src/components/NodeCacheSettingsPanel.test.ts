// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import { ElAlert, ElButton } from 'element-plus'
import { en } from '../locales/en'
import zh from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import es from '../../../../language-packs/packs/es/frontend/messages.json'
import type { ApiNode } from '../types/node'
import NodeCacheSettingsPanel from './NodeCacheSettingsPanel.vue'
import DangerConfirmDialog from './DangerConfirmDialog.vue'
import { bytesToGiBInput, GIB, type CacheTask, type NodeCacheSnapshot } from '../lib/nodeCacheApi'

const mocks = vi.hoisted(() => ({ get: vi.fn(), operate: vi.fn(), task: vi.fn() }))
vi.mock('../lib/nodeCacheApi', async (original) => ({ ...await original<object>(), getNodeCache: mocks.get, operateNodeCache: mocks.operate, getCacheTask: mocks.task }))
const wrappers: ReturnType<typeof mount>[] = []
const current: NodeCacheSnapshot = {
  supported: true,
  policy: { schema_version: 1, revision: 1, blocking_bytes: 10 * GIB, warning_bytes: 20 * GIB },
  application_state: 'applied',
  report: {
    active_cache_root: '/cache/current', mode: 'demand_driven', application_state: 'applied',
    usage: { logical_bytes: 1.234 * GIB, complete: true, sampled_at: '2026-11-24T01:19:07Z', basis: 'logical_file_bytes' },
    space: { available_bytes: 50 * GIB, sampled_at: '2026-11-24T01:19:07Z', state: 'normal' },
  },
}
function setup(snapshot: NodeCacheSnapshot = current, capabilities: string[] = ['cache_management_v1'], exposeTooltip = false, locale: 'en' | 'zh-hans' | 'es' = 'en') {
  mocks.get.mockResolvedValue(snapshot)
  const wrapper = mount(NodeCacheSettingsPanel, {
    props: { node: { id: 1, role: 'proxy', availability: 'online', metadata: { inventory: { capabilities } } } as unknown as ApiNode },
    global: {
      plugins: [createI18n({ legacy: false, locale, messages: { en, 'zh-hans': zh, es } })],
      directives: { 'table-overflow-title': {} },
      stubs: exposeTooltip ? { ElTooltip: { template: '<div><slot name="content" /><slot /></div>' } } : {},
    },
  })
  wrappers.push(wrapper)
  return wrapper
}
function api(wrapper: ReturnType<typeof setup>) {
  return wrapper.vm as unknown as { cachePolicyDirty: () => boolean; cachePolicyError: string; cacheOperationPending: boolean; saveCachePolicy: () => Promise<CacheTask | null>; cancelCacheEdits: () => void }
}
function button(wrapper: ReturnType<typeof setup>, label: string) {
  return wrapper.findAllComponents(ElButton).find(b => b.text() === label || b.attributes('aria-label') === label)!
}
beforeEach(() => {
  mocks.get.mockReset(); mocks.operate.mockReset(); mocks.task.mockReset()
  mocks.operate.mockImplementation(async (_id, body) => ({ id: body.operation, kind: body.operation === 'refresh' ? 'cache.status' : body.operation, status: 'success', result: {} }))
})
afterEach(() => {
  wrappers.splice(0).forEach(w => w.unmount())
  vi.useRealTimers()
})
describe('unified cache management', () => {
  it('keeps legacy configuration without issuing a new cache command', async () => {
    const w = setup({ supported: false, policy: null, application_state: 'unsupported', report: {} }, [])
    await flushPromises()
    expect(w.text()).toContain('legacy fixed-size caching')
    expect(w.text()).toContain('Upgrade the source host or proxy host')
    expect(mocks.operate).not.toHaveBeenCalled()
  })
  it('uses right-side number controls and steps the draft and derived warning by one GiB', async () => {
    const w = setup()
    await flushPromises()
    const input = w.get<HTMLInputElement>('#cache-blocking')
    expect(w.get('.node-cache-blocking-input .el-input-number').classes()).toContain('is-controls-right')
    expect(w.get('.node-cache-blocking-input .hfl-detail-form-input__suffix').text()).toBe('GiB')
    expect(input.attributes('role')).toBe('spinbutton')
    await w.get('button.el-input-number__increase').trigger('click')
    expect(input.element.value).toBe('11')
    expect(w.get('.node-cache-warning-line').text()).toContain('21 GiB')
    await w.get('button.el-input-number__decrease').trigger('click')
    expect(input.element.value).toBe('10')
    expect(api(w).cachePolicyDirty()).toBe(false)
    await input.trigger('keydown', { key: 'ArrowUp' })
    expect(input.element.value).toBe('11')
    await input.trigger('keydown', { key: 'ArrowDown' })
    expect(input.element.value).toBe('10')
    expect(mocks.operate).toHaveBeenCalledTimes(1) // No automatic save.
  })
  it('never steps to zero or beyond the byte-safe policy limit', async () => {
    const w = setup()
    await flushPromises()
    const input = w.get<HTMLInputElement>('#cache-blocking')
    await input.setValue('1')
    expect(w.get<HTMLButtonElement>('button.el-input-number__decrease').element.disabled).toBe(true)
    await input.trigger('keydown', { key: 'ArrowDown' })
    expect(input.element.value).toBe('1')
    await input.setValue('0.5')
    await w.get('button.el-input-number__increase').trigger('click')
    expect(input.element.value).toBe('1.5')
    const max = Number.MAX_SAFE_INTEGER - 10 * GIB
    await input.setValue(bytesToGiBInput(max))
    expect(w.get<HTMLButtonElement>('button.el-input-number__increase').element.disabled).toBe(true)
    await input.trigger('keydown', { key: 'ArrowUp' })
    expect(input.element.value).toBe(bytesToGiBInput(max))
  })
  it('preserves every byte of a legacy fractional GiB policy across arrow steps and cancellation', async () => {
    const w = setup({ ...current, policy: { schema_version: 1, revision: 7, blocking_bytes: 10_000_000_000, warning_bytes: 20_000_000_000 } })
    await flushPromises()
    const input = w.get<HTMLInputElement>('#cache-blocking')
    expect(input.element.value).toBe('9.31322574615478515625')
    expect(api(w).cachePolicyDirty()).toBe(false)
    await w.get('button.el-input-number__increase').trigger('click')
    expect(input.element.value).toBe('10.31322574615478515625')
    await w.get('button.el-input-number__decrease').trigger('click')
    expect(input.element.value).toBe('9.31322574615478515625')
    expect(api(w).cachePolicyDirty()).toBe(false)
    await w.get('button.el-input-number__increase').trigger('click')
    api(w).cancelCacheEdits()
    await flushPromises()
    expect(input.element.value).toBe('9.31322574615478515625')
    expect(mocks.operate).toHaveBeenCalledTimes(1)
  })
  it('disables both arrow buttons and keyboard steps while the unified form is saving', async () => {
    const w = setup()
    await flushPromises()
    await w.setProps({ saving: true })
    expect(w.get<HTMLButtonElement>('button.el-input-number__increase').element.disabled).toBe(true)
    expect(w.get<HTMLButtonElement>('button.el-input-number__decrease').element.disabled).toBe(true)
    await w.get('#cache-blocking').trigger('keydown', { key: 'ArrowUp' })
    expect(w.get<HTMLInputElement>('#cache-blocking').element.value).toBe('10')
  })
  it('groups directory, usage, free space and sampling time with no redundant headings or save button', async () => {
    const w = setup()
    await flushPromises()
    expect(w.get('.node-cache-root').text()).toContain('/cache/current')
    expect(w.get('.node-cache-space-lines').text()).toContain('1.234 GiB')
    expect(w.get('.node-cache-space-lines').text()).toContain('50 GiB')
    expect(w.get('.node-cache-space-lines').text()).toContain('Last checked')
    expect(w.get('.node-cache-warning-line').text()).toContain('20 GiB (Warning threshold = blocking threshold + 10 GiB)')
    expect(w.find('.node-cache-directory-editor').exists()).toBe(false)
    expect(w.text()).not.toContain('Policy Application')
    expect(w.text()).not.toContain('Validate Destination')
    expect(w.text()).not.toContain('Save Cache Policy')
    expect(w.findAll('h4').map(h => h.text())).toEqual(['Cache Management'])
    expect(w.get('.node-cache-blocking-group .node-cache-help').attributes('aria-label')).toBe('Backup-blocking Threshold')
  })
  it('explains the current directory with the same structured info style and no extra cache operation', async () => {
    const w = setup(current, ['cache_management_v1'], true)
    await flushPromises()
    const heading = w.get('.node-cache-root-row .node-cache-group__heading')
    expect(heading.get('.node-cache-directory-help-trigger').attributes('aria-label')).toBe('Current Cache Directory')
    expect(heading.findAll('button')).toHaveLength(1)
    const help = heading.get('.node-cache-directory-help')
    expect(help.classes()).toContain('node-cache-threshold-help')
    expect(help.findAll('p').map(p => p.text())).toEqual([
      en.nodeCache.directoryHelp.purpose, en.nodeCache.directoryHelp.heading, en.nodeCache.directoryHelp.note,
    ])
    expect(help.findAll('li').map(li => li.text())).toEqual([
      en.nodeCache.directoryHelp.backup, en.nodeCache.directoryHelp.disk, en.nodeCache.directoryHelp.relocate,
    ])
    expect(w.get('.node-cache-root-row').findComponent(ElButton).text()).toBe('Change Cache Directory')
    expect(mocks.operate).toHaveBeenCalledTimes(1) // Only the usual entry measurement.
    expect(w.find('.node-cache-directory-editor').exists()).toBe(false)
  })
  it('uses three sibling groups with indented content and plain-weight space labels', async () => {
    const w = setup()
    await flushPromises()
    const groups = w.findAll('.node-cache-groups > .node-cache-group')
    expect(groups.map(group => group.get('.node-cache-group__title').text())).toEqual([
      'Current Cache Directory', 'Backup-blocking Threshold', 'Warning Threshold',
    ])
    expect(groups.every(group => group.find('.node-cache-group__body').exists())).toBe(true)
    expect(groups[0].get('.node-cache-group__body').find('code').text()).toBe('/cache/current')
    expect(groups[0].get('.node-cache-group__body').find('.node-cache-space-lines').exists()).toBe(true)
    expect(groups[0].get('.node-cache-root-row').findComponent(ElButton).text()).toBe('Change Cache Directory')
    expect(groups[1].get('.node-cache-group__body').find('#cache-blocking').exists()).toBe(true)
    expect(groups[2].get('.node-cache-group__body').text()).toBe('20 GiB (Warning threshold = blocking threshold + 10 GiB)')
    const labels = groups[0].findAll('dt')
    expect(labels).toHaveLength(3)
    expect(labels.every(label => label.find('strong, b').exists() === false)).toBe(true)
  })
  it('places a single accessible refresh icon beside the last-check time rather than on a separate row', async () => {
    const w = setup()
    await flushPromises()
    const refresh = button(w, 'Refresh Cache Status')
    expect(w.findAll('.node-cache-refresh')).toHaveLength(1)
    expect(w.get('.node-cache-last-check').findComponent(ElButton).element).toBe(refresh.element)
    expect(refresh.props()).toMatchObject({ text: true, circle: true, size: 'small' })
    expect(refresh.attributes('title')).toBe('Refresh Cache Status')
    expect(refresh.text()).toBe('')
    expect(mocks.operate).toHaveBeenCalledTimes(1) // Entry measurement remains unchanged.
    await refresh.trigger('click')
    await flushPromises()
    expect(mocks.operate).toHaveBeenCalledTimes(2)
    await w.setProps({ node: { ...w.props('node'), availability: 'offline' } })
    expect(button(w, 'Refresh Cache Status').props('disabled')).toBe(true)
    expect(mocks.operate).toHaveBeenCalledTimes(2)
  })
  it('refreshes quietly with only the icon spinner while the status task is pending or running', async () => {
    vi.useFakeTimers()
    const w = setup()
    await flushPromises()
    mocks.operate.mockResolvedValueOnce({ id: 'refresh-task', kind: 'cache.status', status: 'pending' })
    mocks.task.mockResolvedValueOnce({ id: 'refresh-task', kind: 'cache.status', status: 'running' })
      .mockResolvedValueOnce({ id: 'refresh-task', kind: 'cache.status', status: 'success' })
    await button(w, 'Refresh Cache Status').trigger('click')
    await flushPromises()
    expect(button(w, 'Refresh Cache Status').props('loading')).toBe(true)
    expect(w.text()).not.toContain(en.nodeCache.operationPending)
    await vi.advanceTimersByTimeAsync(1500)
    await flushPromises()
    expect(button(w, 'Refresh Cache Status').props('loading')).toBe(true)
    expect(w.text()).not.toContain(en.nodeCache.operationPending)
    await vi.advanceTimersByTimeAsync(1500)
    await flushPromises()
    expect(button(w, 'Refresh Cache Status').props('loading')).toBe(false)
    expect(w.text()).not.toContain(en.nodeCache.operationPending)
    expect(mocks.task).toHaveBeenCalledTimes(2)
  })
  it('keeps a threshold draft editable during refresh without treating waiting as a validation error', async () => {
    const w = setup()
    await flushPromises()
    let finish!: (task: CacheTask) => void
    mocks.operate.mockImplementationOnce(() => new Promise<CacheTask>(resolve => { finish = resolve }))
    await w.get('#cache-blocking').setValue('12')
    await button(w, 'Refresh Cache Status').trigger('click')
    await flushPromises()
    expect(api(w).cacheOperationPending).toBe(true)
    expect(api(w).cachePolicyError).toBe('')
    expect(w.get<HTMLInputElement>('#cache-blocking').element.disabled).toBe(false)
    expect(w.get('.node-cache-policy-body [role="status"]').text()).toBe(en.nodeCache.refreshingStatus)
    expect(w.find('.node-cache-policy-body [role="alert"]').exists()).toBe(false)
    await expect(api(w).saveCachePolicy()).rejects.toThrow(en.nodeCache.operationInProgress)
    expect(mocks.operate).toHaveBeenCalledTimes(2) // Initial refresh and explicit refresh, no policy write.
    finish({ id: 'refreshed', kind: 'cache.status', status: 'success' })
    await flushPromises()
    expect(api(w).cacheOperationPending).toBe(false)
    expect(w.get<HTMLInputElement>('#cache-blocking').element.value).toBe('12')
    expect(w.find('.node-cache-policy-body [role="status"]').exists()).toBe(false)
  })
  it('never flashes the wait-for-operation error while saving its own threshold', async () => {
    const w = setup()
    await flushPromises()
    await w.get('#cache-blocking').setValue('12')
    let finish!: (task: CacheTask) => void
    mocks.operate.mockImplementationOnce(() => new Promise<CacheTask>(resolve => { finish = resolve }))
    const save = api(w).saveCachePolicy()
    await flushPromises()
    expect(api(w).cachePolicyError).toBe('')
    expect(w.find('.node-cache-policy-body [role="alert"]').exists()).toBe(false)
    expect(w.text()).not.toContain(en.nodeCache.waitForOperation)
    expect(w.get<HTMLInputElement>('#cache-blocking').element.disabled).toBe(true)
    finish({ id: 'saved', kind: 'cache.policy.apply', status: 'success' })
    await save
    expect(api(w).cachePolicyDirty()).toBe(false)
  })
  it('uses a neutral directory-operation notice and keeps threshold edits locked', async () => {
    vi.useFakeTimers()
    const w = setup({ ...current, last_task: { id: 'relocating', kind: 'cache.relocate', status: 'running' } })
    await flushPromises()
    expect(w.get<HTMLInputElement>('#cache-blocking').element.disabled).toBe(true)
    expect(w.get('.node-cache-policy-body [role="status"]').text()).toBe(en.nodeCache.directoryOperationPending)
    expect(w.find('.node-cache-policy-body [role="alert"]').exists()).toBe(false)
    expect(api(w).cacheOperationPending).toBe(true)
  })
  it('never shows the reopen-page notice while saving a threshold, even after the polling deadline', async () => {
    vi.useFakeTimers()
    const w = setup()
    await flushPromises()
    await w.get('#cache-blocking').setValue('12')
    const pending: CacheTask = { id: 'policy-save', kind: 'cache.policy.apply', status: 'pending' }
    mocks.operate.mockResolvedValueOnce(pending)
    mocks.task.mockResolvedValue({ ...pending, status: 'running' })
    const save = api(w).saveCachePolicy()
    await flushPromises()
    expect(w.text()).not.toContain(en.nodeCache.operationPending)
    await vi.advanceTimersByTimeAsync(120_000)
    await save
    await flushPromises()
    expect(w.text()).not.toContain(en.nodeCache.operationPending)
    expect(w.find('.node-cache-error').exists()).toBe(false)
    expect(api(w).cacheOperationPending).toBe(true) // Do not remove concurrency protection.
  })
  it('does not show the reopen-page notice when reattaching to a pending policy application', async () => {
    vi.useFakeTimers()
    const pending: CacheTask = { id: 'policy-existing', kind: 'cache.policy.apply', status: 'running' }
    mocks.task.mockResolvedValue(pending)
    const w = setup({ ...current, last_task: pending })
    await flushPromises()
    expect(w.text()).not.toContain(en.nodeCache.operationPending)
    expect(mocks.operate).not.toHaveBeenCalled()
  })
  it('keeps the pending-operation notice for a long-running directory change', async () => {
    vi.useFakeTimers()
    const w = setup({ ...current, last_task: { id: 'relocating', kind: 'cache.relocate', status: 'running' } })
    await flushPromises()
    expect(w.text()).toContain(en.nodeCache.operationPending)
    expect(mocks.operate).not.toHaveBeenCalled() // Reattach, do not duplicate the task.
  })
  it('renders threshold help as real paragraphs and separate list items rather than newline-dependent text', async () => {
    const w = setup(current, ['cache_management_v1'], true)
    await flushPromises()
    const help = w.get('.node-cache-blocking-group .node-cache-threshold-help')
    expect(help.findAll('p').map(p => p.text())).toEqual([
      en.nodeCache.blockingHelp.purpose,
      en.nodeCache.blockingHelp.example,
      en.nodeCache.blockingHelp.note,
    ])
    expect(help.findAll('li').map(li => li.text())).toEqual([
      en.nodeCache.blockingHelp.blocked,
      en.nodeCache.blockingHelp.warning,
      en.nodeCache.blockingHelp.normal,
    ])
    expect(Object.values(en.nodeCache.blockingHelp).every(text => !text.includes('\n'))).toBe(true)
    expect(w.text()).not.toContain('Space Policy')
  })
  it('adds a warning-threshold info button with the same structured help layout and accurate boundaries', async () => {
    const w = setup(current, ['cache_management_v1'], true)
    await flushPromises()
    const warning = w.get('.node-cache-warning-group')
    expect(warning.findAll('button')).toHaveLength(1)
    expect(warning.get('button').attributes('aria-label')).toBe('Warning Threshold')
    const help = warning.get('.node-cache-warning-help')
    expect(help.classes()).toContain('node-cache-threshold-help')
    expect(help.findAll('p').map(p => p.text())).toEqual([
      en.nodeCache.warningHelp.purpose,
      en.nodeCache.warningHelp.formula,
      en.nodeCache.warningHelp.example,
      en.nodeCache.warningHelp.note,
    ])
    expect(help.findAll('li').map(li => li.text())).toEqual([
      en.nodeCache.warningHelp.blocked,
      en.nodeCache.warningHelp.warning,
      en.nodeCache.warningHelp.normal,
    ])
    expect(warning.get('.node-cache-group__title').text()).toBe('Warning Threshold')
    expect(warning.get('.node-cache-warning-line').text()).toBe('20 GiB (Warning threshold = blocking threshold + 10 GiB)')
    expect(mocks.operate).toHaveBeenCalledTimes(1) // Help does not schedule any extra work.
  })
  it('measures once per tab entry but not on a node metadata update or timer', async () => {
    vi.useFakeTimers()
    const w = setup()
    await flushPromises()
    expect(mocks.operate).toHaveBeenCalledTimes(1)
    expect(mocks.operate).toHaveBeenCalledWith(1, { operation: 'refresh' }, expect.any(AbortSignal))
    await w.setProps({ node: { ...w.props('node'), version: 'new-heartbeat' } })
    await vi.advanceTimersByTimeAsync(24_000)
    expect(mocks.operate).toHaveBeenCalledTimes(1)
    await w.setProps({ active: false })
    await w.setProps({ active: true })
    await flushPromises()
    expect(mocks.operate).toHaveBeenCalledTimes(2)
  })
  it('retains decimal-GB bytes and warning gap without marking the draft dirty or writing policy', async () => {
    const w = setup({ ...current, policy: { schema_version: 1, revision: 7, blocking_bytes: 10_000_000_000, warning_bytes: 20_000_000_000 } })
    await flushPromises()
    expect(w.get<HTMLInputElement>('#cache-blocking').element.value).toBe('9.31322574615478515625')
    expect(w.get('.node-cache-warning-line').text()).toContain('18.626 GiB')
    expect(w.get('.node-cache-warning-line').text()).toContain('Retained policy')
    expect(api(w).cachePolicyDirty()).toBe(false)
    await api(w).saveCachePolicy()
    expect(mocks.operate).toHaveBeenCalledTimes(1) // Measurement only.
  })
  it('keeps edited thresholds through refresh; cancel restores exact saved bytes', async () => {
    const w = setup()
    await flushPromises()
    await w.get('#cache-blocking').setValue('12.5')
    await button(w, 'Refresh Cache Status').trigger('click')
    await flushPromises()
    expect(w.get<HTMLInputElement>('#cache-blocking').element.value).toBe('12.5')
    expect(api(w).cachePolicyDirty()).toBe(true)
    api(w).cancelCacheEdits()
    await flushPromises()
    expect(w.get<HTMLInputElement>('#cache-blocking').element.value).toBe('10')
    expect(api(w).cachePolicyDirty()).toBe(false)
  })
  it('does not submit invalid thresholds; a valid explicit save uses binary bytes', async () => {
    const w = setup()
    await flushPromises()
    await w.get('#cache-blocking').setValue('')
    await expect(api(w).saveCachePolicy()).rejects.toThrow('GiB')
    expect(mocks.operate).toHaveBeenCalledTimes(1)
    await w.get('#cache-blocking').setValue('12.5')
    await api(w).saveCachePolicy()
    expect(mocks.operate).toHaveBeenLastCalledWith(1, { operation: 'save_policy', blocking_bytes: 12.5 * GIB }, expect.any(AbortSignal))
  })
  it('expands an inline target row and confirms validation with loading before dangerous relocation', async () => {
    const w = setup()
    await flushPromises()
    await button(w, 'Change Cache Directory').trigger('click')
    await w.get('#cache-destination').setValue('/target')
    let finish!: (task: CacheTask) => void
    mocks.operate.mockImplementationOnce(() => new Promise<CacheTask>(resolve => { finish = resolve }))
    await button(w, 'Confirm').trigger('click')
    expect(button(w, 'Validating and changing…').props('loading')).toBe(true)
    expect(mocks.operate).toHaveBeenLastCalledWith(1, { operation: 'validate_destination', parent: '/target' }, expect.any(AbortSignal))
    finish({ id: 'validated-id', kind: 'cache.relocation.validate', status: 'success', result: { destination: '/target/installation-cache', validation_token: 'local-token' } })
    await flushPromises()
    expect(w.findComponent(DangerConfirmDialog).props('modelValue')).toBe(true)
    expect(mocks.operate).toHaveBeenCalledTimes(2) // No relocation before confirmation.
    await w.findComponent(DangerConfirmDialog).vm.$emit('confirm')
    await flushPromises()
    expect(mocks.operate).toHaveBeenLastCalledWith(1, { operation: 'relocate', validation_task_id: 'validated-id' }, expect.any(AbortSignal))
    expect(w.find('.node-cache-directory-editor').exists()).toBe(false)
  })
  it('hides a completed historical relocation and shows the effective directory once', async () => {
    const destination = '/target/current-cache'
    const w = setup({ ...current, report: { ...current.report, active_cache_root: destination,
      relocation: { task_id: 'completed-task', stage: 'completed', destination },
    } })
    await flushPromises()
    expect(w.get('.node-cache-root > .node-cache-path').text()).toBe(destination)
    expect(w.find('.node-cache-relocation-status').exists()).toBe(false)
    expect(w.text()).not.toContain('Completed')
    expect(w.text().split(destination)).toHaveLength(2)
  })
  it('updates the effective directory on success without retaining a completed result block', async () => {
    const w = setup()
    await flushPromises()
    await button(w, 'Change Cache Directory').trigger('click')
    await w.get('#cache-destination').setValue('/target')
    mocks.operate.mockResolvedValueOnce({ id: 'validation', kind: 'cache.relocation.validate', status: 'success', result: { destination: '/target/new-cache' } })
    await button(w, 'Confirm').trigger('click')
    await flushPromises()
    const relocation = { task_id: 'cutover', stage: 'completed', source: '/cache/current', destination: '/target/new-cache' }
    mocks.get.mockResolvedValue({ ...current, report: { ...current.report, active_cache_root: relocation.destination, relocation } })
    mocks.operate.mockResolvedValueOnce({ id: 'cutover', kind: 'cache.relocate', status: 'success', result: relocation })
    await w.findComponent(DangerConfirmDialog).vm.$emit('confirm')
    await flushPromises()
    expect(w.get('.node-cache-root > .node-cache-path').text()).toBe(relocation.destination)
    expect(w.find('.node-cache-relocation-status').exists()).toBe(false)
    expect(w.find('.node-cache-directory-editor').exists()).toBe(false)
    expect(w.text()).not.toContain('/cache/current')
    expect(w.text().split(relocation.destination)).toHaveLength(2)
  })
  it.each(['verifying', 'failed', 'switched_cleanup_pending'])('keeps unfinished or failed relocation status visible (%s)', async (stage) => {
    const w = setup({ ...current, report: { ...current.report, relocation: { task_id: 'unfinished', stage, destination: '/target/cache', error: stage === 'failed' ? 'Directory verification failed' : '' } } })
    await flushPromises()
    expect(w.get('.node-cache-relocation-status').text()).toContain('/target/cache')
    expect(w.get('.node-cache-relocation-status').text()).toContain(en.nodeCache.stages[stage as keyof typeof en.nodeCache.stages])
    expect(w.findAllComponents(ElButton).some(b => b.text() === 'Retry Old Cache Cleanup')).toBe(stage === 'switched_cleanup_pending')
  })
  it('shows the current relocation failure once without suppressing independent errors', async () => {
    const w = setup()
    await flushPromises()
    await button(w, 'Change Cache Directory').trigger('click')
    await w.get('#cache-destination').setValue('/target')
    mocks.operate.mockResolvedValueOnce({ id: 'validated', kind: 'cache.relocation.validate', status: 'success', result: { destination: '/target/owned', validation_token: 'fixture' } })
    await button(w, 'Confirm').trigger('click')
    await flushPromises()
    const failure = 'New cache directory is not writable'
    const relocation = { task_id: 'relocate-failed', stage: 'failed', source: '/cache/current', destination: '/target/owned', error: failure }
    mocks.get.mockResolvedValue({ ...current, report: { ...current.report, relocation } })
    mocks.operate.mockResolvedValueOnce({ id: 'relocate-failed', kind: 'cache.relocate', status: 'failed', error: failure })
    await w.findComponent(DangerConfirmDialog).vm.$emit('confirm')
    await flushPromises()
    expect(w.text().split(failure)).toHaveLength(2) // One occurrence, not two.
    expect(w.findAll('.el-alert--error')).toHaveLength(1)
    expect(w.find('.node-cache-general-error').exists()).toBe(false)
    expect(w.get('.node-cache-relocation-status').text()).toContain('Old Directory Retained')
    mocks.operate.mockResolvedValueOnce({ id: 'refresh-failed', kind: 'cache.status', status: 'failed', error: failure })
    await button(w, 'Refresh Cache Status').trigger('click')
    await flushPromises()
    expect(w.get('.node-cache-general-error').text()).toContain(failure) // Different task remains visible.
  })
  it.each([['en', en], ['zh-hans', zh], ['es', es]] as const)('shows localized cleanup warnings in an independent box below the target editor in %s', async (locale, catalog) => {
    const source = 'C:\\ProgramData\\HyperFileLens\\Agent\\cache'
    const destination = 'E:\\cache\\installation-cache'
    const diagnostic = `remove ${source}: The process cannot access the file because it is being used by another process.`
    const relocation = { task_id: 'cleanup-pending', stage: 'switched_cleanup_pending', source, destination, error: diagnostic }
    const w = setup({ ...current, report: { ...current.report, active_cache_root: destination, relocation } }, ['cache_management_v1'], false, locale)
    await flushPromises()
    await button(w, catalog.nodeCache.changeDirectory).trigger('click')
    const box = w.get('.node-cache-relocation-status')
    expect(w.findAllComponents(ElAlert).find(alert => alert.props('title') === catalog.nodeCache.stages.switched_cleanup_pending)?.props('type')).toBe('warning')
    expect(w.get('.node-cache-directory-editor').element.nextElementSibling).toBe(box.element)
    expect(box.element.nextElementSibling?.tagName).toBe('DL')
    expect(box.text()).toContain(catalog.nodeCache.stages.switched_cleanup_pending)
    expect(box.text()).toContain(catalog.nodeCache.cleanupPendingHelp)
    expect(box.text()).toContain(catalog.nodeCache.errors.directoryInUse)
    expect(box.text()).toContain(source)
    expect(box.text()).toContain(destination)
    expect(box.get('.node-cache-technical pre').text()).toBe(diagnostic)
    expect(box.get('.node-cache-technical').attributes('open')).toBeUndefined()
    expect(box.get('.node-cache-relocation-status__body > p').text()).not.toContain('The process cannot access')
    expect(w.find('.node-cache-general-error').exists()).toBe(false)
    expect(button(w, catalog.nodeCache.retryCleanup)).toBeTruthy()
    expect(mocks.operate).toHaveBeenCalledTimes(1) // No automatic cleanup.
  })
  it('removes the cleanup warning after an explicit retry succeeds while keeping the new root', async () => {
    const relocation = { task_id: 'old-operation', stage: 'switched_cleanup_pending', source: '/old-cache', destination: '/new-cache', error: 'Old cache still in use' }
    const remote = { ...current, report: { ...current.report, active_cache_root: relocation.destination, relocation } }
    const w = setup(remote)
    await flushPromises()
    expect(w.find('.node-cache-relocation-status').exists()).toBe(true)
    mocks.get.mockResolvedValue({ ...remote, report: { ...remote.report, relocation: { ...relocation, stage: 'completed', error: '' } } })
    mocks.operate.mockResolvedValueOnce({ id: 'cleanup-retry', kind: 'cache.relocation.retry', status: 'success', result: { ...relocation, stage: 'completed', error: '' } })
    await button(w, en.nodeCache.retryCleanup).trigger('click')
    await flushPromises()
    expect(w.find('.node-cache-relocation-status').exists()).toBe(false)
    expect(w.get('.node-cache-root > .node-cache-path').text()).toBe('/new-cache')
    expect(mocks.operate).toHaveBeenLastCalledWith(1, { operation: 'retry_cleanup' }, expect.any(AbortSignal))
  })
  it('never relocates on validation failure and preserves the entered path for retry', async () => {
    const w = setup()
    await flushPromises()
    await button(w, 'Change Cache Directory').trigger('click')
    await w.get('#cache-destination').setValue('/bad')
    mocks.operate.mockResolvedValueOnce({ id: 'bad', kind: 'cache.relocation.validate', status: 'failed', error: 'Path not writable' })
    await button(w, 'Confirm').trigger('click')
    await flushPromises()
    expect(w.get('#cache-destination-error').text()).toContain('Path not writable')
    expect(w.get('.node-cache-directory-editor__row').element.nextElementSibling?.id).toBe('cache-destination-error')
    expect(w.findAll('.el-alert--error')).toHaveLength(1)
    expect(w.find('.node-cache-general-error').exists()).toBe(false)
    expect(w.get('#cache-destination').attributes('aria-invalid')).toBe('true')
    expect(w.get('#cache-destination').attributes('aria-describedby')).toBe('cache-destination-error')
    expect(w.get<HTMLInputElement>('#cache-destination').element.value).toBe('/bad')
    expect(w.findComponent(DangerConfirmDialog).props('modelValue')).toBe(false)
    expect(mocks.operate.mock.calls.every(([, b]) => b.operation !== 'relocate')).toBe(true)
    await button(w, 'Cancel').trigger('click')
    expect(w.find('.node-cache-directory-editor').exists()).toBe(false)
    expect(w.text()).not.toContain('Path not writable')
    expect(w.findAll('.el-alert--error')).toHaveLength(0)
  })
  it('clears a directory-specific error when the path changes, without moving it to the panel footer', async () => {
    const w = setup()
    await flushPromises()
    await button(w, 'Change Cache Directory').trigger('click')
    await w.get('#cache-destination').setValue('/protected-installation')
    const failure = 'destination overlaps protected installation, repository or log data'
    mocks.operate.mockResolvedValueOnce({ id: 'invalid-target', kind: 'cache.relocation.validate', status: 'failed', error: failure })
    await button(w, 'Confirm').trigger('click')
    await flushPromises()
    expect(w.get('#cache-destination-error').text()).toContain(en.nodeCache.errors.protectedPath)
    expect(w.text().split(en.nodeCache.errors.protectedPath)).toHaveLength(2)
    await w.get('#cache-destination').setValue('/another-target')
    expect(w.find('#cache-destination-error').exists()).toBe(false)
    expect(w.get('#cache-destination').attributes('aria-invalid')).toBeUndefined()
    expect(w.text()).not.toContain(en.nodeCache.errors.protectedPath)
    expect(w.findAll('.el-alert--error')).toHaveLength(0)
  })
  it('routes validation request failures to the field even before a task is returned, but not refresh failures', async () => {
    const w = setup()
    await flushPromises()
    await button(w, 'Change Cache Directory').trigger('click')
    await w.get('#cache-destination').setValue('/target')
    mocks.operate.mockRejectedValueOnce(new Error('Validation request unavailable'))
    await button(w, 'Confirm').trigger('click')
    await flushPromises()
    expect(w.get('#cache-destination-error').text().length).toBeGreaterThan(0)
    expect(w.findAll('.el-alert--error')).toHaveLength(1)
    expect(w.find('.node-cache-general-error').exists()).toBe(false)
    expect(w.findComponent(DangerConfirmDialog).props('modelValue')).toBe(false)
    mocks.operate.mockRejectedValueOnce(new Error('Refresh request unavailable'))
    await button(w, 'Refresh Cache Status').trigger('click')
    await flushPromises()
    expect(w.find('#cache-destination-error').exists()).toBe(false)
    expect(w.get('.el-alert--error').text().length).toBeGreaterThan(0)
  })
  it('keeps older cache binaries read-only instead of sending an unsupported binary policy', async () => {
    const w = setup({ ...current, policy_unit_supported: false })
    await flushPromises()
    expect(w.get<HTMLInputElement>('#cache-blocking').element.disabled).toBe(true)
    expect(w.text()).not.toContain('Upgrade the source host or proxy host to edit GiB thresholds')
    expect(api(w).cachePolicyDirty()).toBe(false)
  })
  it('allows repeated saves and remains editable after a refresh reports confirmed GiB support', async () => {
    let remote = { ...current, policy_unit_supported: true }
    const w = setup(remote)
    mocks.get.mockImplementation(async () => remote)
    await flushPromises()
    for (const value of [12, 14]) {
      expect(w.get<HTMLInputElement>('#cache-blocking').element.disabled).toBe(false)
      await w.get('#cache-blocking').setValue(String(value))
      mocks.operate.mockImplementationOnce(async (_id, body) => {
        remote = { ...remote, policy: { ...remote.policy!, blocking_bytes: body.blocking_bytes, warning_bytes: body.blocking_bytes + 10 * GIB } }
        return { id: 'applied', kind: 'cache.policy.apply', status: 'success' }
      })
      await api(w).saveCachePolicy()
      await flushPromises()
      expect(api(w).cachePolicyDirty()).toBe(false)
      await button(w, 'Refresh Cache Status').trigger('click')
      await flushPromises()
      expect(w.get<HTMLInputElement>('#cache-blocking').element.disabled).toBe(false)
      expect(w.text()).not.toContain('Upgrade the source host or proxy host to edit GiB thresholds')
    }
  })
  it('ignores a response belonging to a closed tab and never starts its stale measurement', async () => {
    let finish!: (value: NodeCacheSnapshot) => void
    mocks.get.mockImplementationOnce(() => new Promise<NodeCacheSnapshot>(resolve => { finish = resolve }))
    const w = setup()
    await w.setProps({ active: false })
    finish(current)
    await flushPromises()
    expect(mocks.operate).not.toHaveBeenCalled()
    await w.setProps({ active: true })
    await flushPromises()
    expect(mocks.operate).toHaveBeenCalledTimes(1)
  })
  it('does not turn unknown space into zero or invent a current timestamp', async () => {
    const w = setup({ ...current, report: { active_cache_root: '/cache', space: { available_bytes: null, state: 'check_error', sampled_at: '' }, space_error: 'Mount unavailable' } })
    await flushPromises()
    expect(w.get('.node-cache-space-lines').text()).not.toContain('0 GiB')
    expect(w.get('.node-cache-space-lines').text()).toContain('—')
    expect(w.text()).toContain('Mount unavailable')
  })
})
