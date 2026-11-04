// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import AlertPolicyEditorPage, { type AlertPolicyFormState } from './AlertPolicyEditorPage.vue'

const mocks = vi.hoisted(() => ({
  route: { params: {} as { id?: string }, path: '/ops/alerts/rules/create' }, push: vi.fn(),
  getPolicy: vi.fn(), createPolicy: vi.fn(), updatePolicy: vi.fn(),
  fetchMetadata: vi.fn(), fetchMetadataResources: vi.fn(), listChannels: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('../../lib/alertApi', () => ({
  getPolicy: mocks.getPolicy, createPolicy: mocks.createPolicy, updatePolicy: mocks.updatePolicy,
  fetchMetadata: mocks.fetchMetadata, fetchMetadataResources: mocks.fetchMetadataResources,
}))
vi.mock('../../lib/notificationApi', () => ({ listChannels: mocks.listChannels }))
vi.mock('../../lib/api', () => ({ apiErrorMessageI18n: () => 'Request failed' }))
vi.mock('../../platform-ops/lib/platformOpsApi', () => ({
  createPlatformAlertPolicy: vi.fn(),
  fetchPlatformAlertMetadataMetrics: vi.fn(),
  fetchPlatformAlertMetadataResources: vi.fn(),
  fetchPlatformAlertPolicy: vi.fn(),
  listPlatformNotificationChannelOptions: vi.fn(),
  updatePlatformAlertPolicy: vi.fn(),
}))

const wrappers: ReturnType<typeof mount>[] = []
function catalog(locale: string) {
  return locale === 'en' ? en : JSON.parse(readFileSync(resolve(
    process.cwd(), `../../language-packs/packs/${locale}/frontend/messages.json`,
  ), 'utf8'))
}

async function render(locale = 'en') {
  const wrapper = mount(AlertPolicyEditorPage, {
    global: { plugins: [ElementPlus, createI18n({
      legacy: false, locale, fallbackLocale: 'en', messages: { en, [locale]: catalog(locale) },
    })] },
  })
  wrappers.push(wrapper)
  await flushPromises()
  const state = wrapper.vm.$.setupState as unknown as {
    form: AlertPolicyFormState
    policyPayload: () => Record<string, unknown>
    savePolicy: () => Promise<void>
    selectableResourceTypeOptions: Array<{ value: string; label: string }>
  }
  return { wrapper, state }
}

function existing(type: string, recovery: Record<string, unknown> | null, trigger?: Record<string, unknown>) {
  mocks.route.params.id = 'existing-policy'
  mocks.getPolicy.mockResolvedValue({
    id: 'existing-policy', name: 'Existing rule', type, severity: 'warning', enabled: true,
    resource_type: 'system', scope: 'all', resource_ids: [], notification_channel_ids: [],
    trigger_rule: trigger ?? { metric_key: 'cpu_usage', operator: '>=', threshold: 80, duration_seconds: 300 },
    recovery_rule: recovery,
  })
}

describe('alert policy recovery editor', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    mocks.route.params = {}
    mocks.route.path = '/ops/alerts/rules/create'
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
    Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
    mocks.listChannels.mockResolvedValue({ results: [] })
    mocks.fetchMetadata.mockImplementation((kind: string, params?: { resource_type: string }) => Promise.resolve(
      kind === 'event-types'
        ? { categories: ['configuration', 'user'], types: { user: ['login_failed'] },
          task_event_types: ['task_failed', 'task_timeout'] }
        : kind === 'task-types' ? ['backup', 'restore', 'repository_operation']
          : kind === 'health-check-types' ? ['heartbeat', 'service_health']
        : kind === 'metrics' && !['task', 'system_service'].includes(params?.resource_type || '')
          ? ['cpu_usage', 'memory_usage', 'disk_usage'] : [],
    ))
    mocks.fetchMetadataResources.mockResolvedValue([])
  })
  afterEach(() => {
    wrappers.splice(0).forEach(wrapper => wrapper.unmount())
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    delete (Element.prototype as Partial<Element>).scrollIntoView
  })

  it('switches new task rules to manual resolution and removes numeric leftovers', async () => {
    const { wrapper, state } = await render()
    state.form.recoveryRule.notify_on_resolved = false
    state.form.triggerRule.managed = 'keep-managed-setting'
    state.form.type = 'task'
    await flushPromises()
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.recoveryManual)
    expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.fieldRecoveryThresholdHint)
    expect(state.policyPayload()).toMatchObject({
      recovery_rule: { enabled: false, notify_on_resolved: false },
      trigger_rule: { task_type: 'backup', event_type: 'task_failed', managed: 'keep-managed-setting' },
    })
    expect(state.form.recoveryRule).not.toHaveProperty('threshold')
    expect(state.form.triggerRule).not.toHaveProperty('metric_key')
    expect(state.form.triggerRule).not.toHaveProperty('threshold')
    state.form.name = 'Backup failure'
    await state.savePolicy()
    expect(mocks.createPolicy).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Backup failure', recovery_rule: expect.objectContaining({ enabled: false }),
    }))
  })

  it.each([null, {}, { enabled: true, notify_on_resolved: false }])(
    'preserves legacy metric recovery on unrelated edits: %j', async recovery => {
      existing('metric', recovery)
      const { wrapper, state } = await render()
      expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.recoveryTriggerCleared)
      state.form.name = 'Renamed'
      expect(state.policyPayload().recovery_rule).toEqual(recovery)
      expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.fieldRecoveryThresholdHint)
      await state.savePolicy()
      expect(mocks.updatePolicy).toHaveBeenCalledWith('existing-policy', expect.objectContaining({
        name: 'Renamed', recovery_rule: recovery,
      }))
    },
  )

  it('preserves non-default task trigger and legacy recovery on hydration', async () => {
    const recovery = { enabled: true, threshold: 70, operator: '<', duration_seconds: 180, notify_on_firing: false }
    existing('task', recovery, { task_type: 'restore', event_type: 'task_timeout', operation_type: 'custom' })
    const { wrapper, state } = await render()
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.recoveryLegacyTask)
    expect(state.form.triggerRule.task_type).toBe('restore')
    expect(state.form.triggerRule.event_type).toBe('task_timeout')
    expect(state.policyPayload().recovery_rule).toEqual(recovery)
    const manual = wrapper.findAll('button').find(button => button.text() === en.ops.alertsCenter.editor.recoveryUseManual)!
    await manual.trigger('click')
    expect(state.policyPayload().recovery_rule).toEqual({ enabled: false, notify_on_firing: false })
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.recoveryManual)
  })

  it('keeps loaded event filters and displays manual resolution', async () => {
    existing('event', { enabled: true, threshold: 70 }, { event_category: 'user', event_types: ['login_failed'] })
    const { wrapper, state } = await render()
    expect(state.form.triggerRule.event_types).toEqual(['login_failed'])
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.recoveryManual)
    expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.fieldRecoveryThresholdHint)
  })

  it('does not mark empty Event Types invalid until save', async () => {
    const required = en.ops.alertsCenter.editor.validateTriggerRequired.replace(
      '{field}', en.ops.alertsCenter.editor.eventTypes,
    )
    const { wrapper, state } = await render()
    state.form.type = 'event'
    await flushPromises()
    expect(state.form.triggerRule.event_types).toEqual([])
    expect(wrapper.text()).not.toContain(required)
    expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.metadataSelectAvailable)
    const eventTypesItem = wrapper.get('[data-alert-policy-field="event_types"]')
    expect(eventTypesItem.classes()).not.toContain('is-error')
    state.form.name = 'Config change'
    await state.savePolicy()
    await flushPromises()
    expect(mocks.createPolicy).not.toHaveBeenCalled()
    expect(eventTypesItem.classes()).toContain('is-error')
  })

  it('describes heartbeat recovery without numeric inputs', async () => {
    existing('health', { enabled: true, threshold: 70, duration_seconds: 180 }, {
      check_type: 'heartbeat', timeout_seconds: 300, duration_seconds: 300,
    })
    const { wrapper, state } = await render()
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.recoveryHeartbeatHint)
    expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.fieldRecoveryThresholdHint)
    expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.recoveryManualHint)
    state.form.recoveryRule.enabled = false
    expect(state.policyPayload().recovery_rule).toMatchObject({ enabled: false })
  })

  it('shows the actual effective numeric operator, not a fixed below-threshold label', async () => {
    existing('metric', { threshold: 90, duration_seconds: 0 }, {
      metric_key: 'cpu_usage', operator: '<=', threshold: 20, duration_seconds: 0,
    })
    const { wrapper } = await render()
    expect(wrapper.text()).toContain('CPU Usage > 90% Immediately')
    expect(wrapper.text()).not.toContain('below_threshold')
  })

  it('only adds numeric defaults to a legacy rule after explicit customization', async () => {
    existing('metric', null)
    const { wrapper, state } = await render()
    const customize = wrapper.findAll('button').find(button => button.text() === en.ops.alertsCenter.editor.recoveryCustomize)!
    await customize.trigger('click')
    expect(state.policyPayload().recovery_rule).toEqual({ operator: '<', threshold: 70, duration_seconds: 180 })
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.fieldRecoveryThresholdHint)
  })

  it('updates recovery capability when changing health checks, retaining notification choices', async () => {
    existing('health', { enabled: true, notify_on_resolved: false }, {
      check_type: 'service_health', duration_seconds: 300,
    })
    const { wrapper, state } = await render()
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.recoveryManual)
    state.form.triggerRule.check_type = 'heartbeat'
    state.form.triggerRule.timeout_seconds = 300
    await flushPromises()
    expect(state.form.recoveryRule).toMatchObject({
      enabled: true, notify_on_resolved: false,
    })
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.recoveryHeartbeatHint)
    state.form.triggerRule.check_type = 'service_health'
    await flushPromises()
    expect(state.form.recoveryRule).toEqual({ enabled: false, notify_on_resolved: false })
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.recoveryManual)
  })

  it('lists Metric Resource Types in canonical host-then-data order', async () => {
    existing('metric', null)
    const { wrapper, state } = await render()
    const optionValues = (state.selectableResourceTypeOptions as Array<{ value: string }>)
      .map(opt => opt.value)
    expect(optionValues).toEqual([
      'system',
      'agent_proxy',
      'sync_proxy',
      'gateway',
      'source_resource',
      'backup_repository',
      'target_storage',
    ])
    expect(wrapper.text()).toContain(en.ops.alertsCenter.resourceTypeDescriptions.system)
    state.form.resourceType = 'system'
    await flushPromises()
    await state.savePolicy()
    expect(mocks.updatePolicy).toHaveBeenCalledOnce()
  })

  it('narrows Task Resource Type to Task only when switching Alert Type', async () => {
    existing('metric', null)
    const { state } = await render()
    state.form.type = 'task'
    await flushPromises()
    expect(state.form.resourceType).toBe('task')
    expect(state.selectableResourceTypeOptions.map(opt => opt.value)).toEqual(['task'])
  })

  it('shows loading and blocks saving while the metric catalog is pending', async () => {
    const normal = mocks.fetchMetadata.getMockImplementation()!
    let finish!: (keys: string[]) => void
    mocks.fetchMetadata.mockImplementation((kind: string, ...args: unknown[]) =>
      kind === 'metrics' ? new Promise(resolve => { finish = resolve }) : normal(kind, ...args),
    )
    const { wrapper, state } = await render()
    state.form.name = 'CPU rule'
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.metricLoading)
    await state.savePolicy()
    expect(mocks.createPolicy).not.toHaveBeenCalled()
    finish(['cpu_usage'])
    await flushPromises()
    expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.metricLoading)
    await state.savePolicy()
    expect(mocks.createPolicy).toHaveBeenCalledOnce()
  })

  it('falls back to the local metric catalog when the metadata API fails', async () => {
    const normal = mocks.fetchMetadata.getMockImplementation()!
    mocks.fetchMetadata.mockImplementation((kind: string, ...args: unknown[]) => {
      if (kind === 'metrics') return Promise.reject(new Error('Offline'))
      return normal(kind, ...args)
    })
    const { wrapper, state } = await render()
    state.form.name = 'CPU rule'
    await flushPromises()
    expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.metricLoadFailed)
    expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.metricUnsupported)
    expect(state.form.triggerRule.metric_key).toBeTruthy()
    await state.savePolicy()
    expect(mocks.createPolicy).toHaveBeenCalledOnce()
  })

  it('shows load failure only when no local catalog exists for the resource type', async () => {
    const normal = mocks.fetchMetadata.getMockImplementation()!
    mocks.fetchMetadata.mockImplementation((kind: string, ...args: unknown[]) => {
      if (kind === 'metrics') return Promise.reject(new Error('Offline'))
      return normal(kind, ...args)
    })
    const { wrapper, state } = await render()
    state.form.name = 'CPU rule'
    state.form.resourceType = 'task'
    await flushPromises()
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.metricLoadFailed)
    expect(wrapper.text()).not.toContain(en.ops.alertsCenter.editor.metricUnsupported)
    await state.savePolicy()
    expect(mocks.createPolicy).not.toHaveBeenCalled()
  })

  it('ignores a late metric response for a previously selected resource type', async () => {
    const normal = mocks.fetchMetadata.getMockImplementation()!
    let finish!: (keys: string[]) => void
    mocks.fetchMetadata.mockImplementation((kind: string, params?: { resource_type: string }) =>
      kind === 'metrics' && params?.resource_type === 'gateway'
        ? new Promise(resolve => { finish = resolve }) : normal(kind, params),
    )
    const { wrapper, state } = await render()
    state.form.resourceType = 'gateway'
    await flushPromises()
    state.form.resourceType = 'system_service'
    await flushPromises()
    finish(['cpu_usage'])
    await flushPromises()
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.metricUnsupported)
    state.form.name = 'Unsupported'
    await state.savePolicy()
    expect(mocks.createPolicy).not.toHaveBeenCalled()
  })

  it.each(['en', 'zh-hans', 'es'])('explains manual resolution before its limitations in %s', async locale => {
    existing('task', { enabled: false }, { task_type: 'backup', event_type: 'task_failed' })
    const messages = catalog(locale)
    const copy = messages.ops.alertsCenter.editor
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { wrapper, state } = await render(locale)
    const section = wrapper.findAll('section').find(item =>
      item.find('h3').text() === copy.sectionRecovery,
    )!
    expect(section.exists()).toBe(true)
    expect(section.text()).toContain(copy.recoveryManual)
    expect(section.text()).not.toContain(copy.sectionRecoveryDesc)
    const hints = section.findAll('.fullscreen-form-field__hint')
    expect(hints).toHaveLength(1)
    expect(hints[0].text()).toBe(copy.recoveryManualTaskHint)
    expect(copy.recoveryManualTaskHint).toContain(messages.ops.alerts.resolve)
    expect(wrapper.find('aside').text()).toContain(copy.previewRecovery)
    expect(wrapper.text()).toContain(copy.fieldNotifyOnResolved)
    expect(state.policyPayload().recovery_rule).toEqual({ enabled: false })
    expect(section.text()).not.toContain(copy.fieldRecoveryEnabled)
    expect(section.text()).not.toContain(copy.fieldRecoveryDuration)
    expect(warn).not.toHaveBeenCalled()
  })

  it('keeps automatic resolution instructions separate from manual-only instructions', async () => {
    existing('metric', { enabled: true, operator: '<', threshold: 70, duration_seconds: 180 })
    const { wrapper, state } = await render()
    const copy = en.ops.alertsCenter.editor
    const section = wrapper.findAll('section').find(item =>
      item.find('h3').text() === copy.sectionRecovery,
    )!
    expect(section.text()).not.toContain(copy.sectionRecoveryDesc)
    expect(section.text()).toContain(copy.fieldRecoveryEnabled)
    expect(section.text()).toContain(copy.fieldRecoveryEnabledHint)
    expect(section.text()).toContain(copy.fieldDuration)
    expect(section.text()).not.toContain(copy.recoveryManualTaskHint)
    expect(section.text()).not.toContain(copy.recoveryManualHint)
    expect(section.text()).not.toContain(copy.recoveryManualMeaning)
    expect(state.policyPayload().recovery_rule).toEqual({
      enabled: true, operator: '<', threshold: 70, duration_seconds: 180,
    })
  })

  it('keeps task rules unsaveable when task metadata fails, without hiding the retry action', async () => {
    mocks.fetchMetadata.mockImplementation((kind: string) =>
      kind === 'task-types' ? Promise.reject(new Error('metadata unavailable')) : Promise.resolve(
        kind === 'event-types'
          ? { categories: ['configuration'], types: { configuration: [], }, task_event_types: ['task_failed'] }
          : [],
      ))
    const { wrapper, state } = await render()
    state.form.type = 'task'
    state.form.name = 'Backup failure'
    await flushPromises()
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.metadataLoadFailed.replace(
      '{field}', en.ops.alertsCenter.editor.taskType,
    ))
    expect(wrapper.text()).toContain(en.ops.alertsCenter.editor.metadataRetry)
    await state.savePolicy()
    expect(mocks.createPolicy).not.toHaveBeenCalled()
  })
})
