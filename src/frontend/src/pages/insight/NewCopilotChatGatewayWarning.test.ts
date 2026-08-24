// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineComponent, h, nextTick, ref } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import type { LensCopilotGatewayOption } from '../../lib/lensApi'
import NewCopilotChat from './NewCopilotChat.vue'

const zhHans = JSON.parse(readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'),
  'utf8',
))

const componentSource = readFileSync(
  resolve(process.cwd(), 'src/pages/insight/NewCopilotChat.vue'),
  'utf8',
)

const mocks = vi.hoisted(() => ({
  createCopilotSession: vi.fn(),
  fetchCopilotReadiness: vi.fn(),
  listCopilotGatewayOptions: vi.fn(),
  routerPush: vi.fn(),
  routerReplace: vi.fn(),
  routerResolve: vi.fn(),
  useCopilotSelectionPreview: vi.fn(),
  useKnowledgeSourceForm: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: mocks.routerPush,
    replace: mocks.routerReplace,
    resolve: mocks.routerResolve,
  }),
}))

vi.mock('../../composables/useKnowledgeSourceForm', () => ({
  useKnowledgeSourceForm: mocks.useKnowledgeSourceForm,
}))

vi.mock('../../composables/useCopilotSelectionPreview', () => ({
  useCopilotSelectionPreview: mocks.useCopilotSelectionPreview,
}))

vi.mock('../../lib/api', () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

vi.mock('../../lib/lensApi', () => ({
  createCopilotSession: mocks.createCopilotSession,
  fetchCopilotReadiness: mocks.fetchCopilotReadiness,
  listCopilotGatewayOptions: mocks.listCopilotGatewayOptions,
}))

const HflPopoverStub = defineComponent({
  name: 'HflPopover',
  setup(_props, { slots }) {
    return () => h('div', [slots.reference?.(), slots.default?.()])
  },
})

const publicGateway: LensCopilotGatewayOption = {
  gateway_link_id: 11,
  gateway_id: 101,
  name: 'public-dg-01',
  scope: 'platform',
  is_platform_default: true,
  sidecar_status: 'online',
  online: true,
  hfl_usable: true,
  copilot_eligible: true,
}

const privateGateway: LensCopilotGatewayOption = {
  gateway_link_id: 22,
  gateway_id: 202,
  name: 'private-dg-01',
  scope: 'user',
  is_platform_default: false,
  sidecar_status: 'online',
  online: true,
  hfl_usable: true,
  copilot_eligible: true,
}

type FormScenario = {
  sourceReady?: boolean
  snapshotReady?: boolean
  scopeReady?: boolean
}

type MountScenario = FormScenario & {
  locale?: 'en' | 'zh-hans'
  agentModelReady?: boolean
  gatewayResponse?: Promise<LensCopilotGatewayOption[]> | LensCopilotGatewayOption[]
  selectionStatus?: 'idle' | 'calculating' | 'waiting' | 'error' | 'ready'
  selectionReasons?: string[]
  selectionLimits?: { max_files: number; max_bytes: number }
  organizationCapacity?: {
    applicable: boolean
    used_bytes?: number
    limit_bytes?: number
    remaining_bytes?: number | null
    after_create_bytes?: number | null
  }
}

function formState({
  sourceReady = true,
  snapshotReady = true,
  scopeReady = true,
}: FormScenario = {}) {
  const selectedBackupConfigId = ref<number | null>(sourceReady ? 7 : null)
  const effectiveSnapshotId = ref<number | null>(snapshotReady ? 17 : null)
  return {
    loading: ref(false),
    snapshotLoading: ref(false),
    selectedBackupConfigId,
    snapshotPickerValue: ref<number | string | null>(snapshotReady ? 17 : null),
    backupSourceOptions: ref(sourceReady
      ? [{ backupConfigId: 7, label: 'Production Documents' }]
      : []),
    snapshotsForSelectedBackupSource: ref(snapshotReady
      ? [{
          id: 17,
          created_at: '2026-08-03T08:00:00Z',
          total_size_bytes: 1024,
        }]
      : []),
    SNAPSHOT_PICKER_LATEST: 'latest',
    effectiveSnapshotId,
    snapshotDirectories: ref(snapshotReady ? [{ id: 31 }] : []),
    backupScopeEntries: ref([{
      id: 'scope-1',
      path: scopeReady ? '/documents/contracts' : '',
      directoryId: scopeReady ? 31 : null,
      pathType: 'dir',
      knownFileCount: scopeReady ? 4 : null,
      knownSizeBytes: scopeReady ? 1024 : null,
    }]),
    openBackupScopePickerId: ref<string | null>(null),
    backupScopeTreeRevision: ref(0),
    backupScopeBrowseLoading: ref(false),
    loadSnapshots: vi.fn().mockResolvedValue(undefined),
    loadBackupScopePickerNode: vi.fn(),
    setBackupScopePickerOpen: vi.fn(),
    addBackupScopeEntry: vi.fn(),
    removeBackupScopeEntry: vi.fn(),
    updateBackupScopeEntryInput: vi.fn(),
    validateBackupScopeEntry: vi.fn(),
    validateBackupScopeEntryOnBlur: vi.fn(),
    validateAllBackupScopeEntries: vi.fn().mockResolvedValue(true),
    pickBackupScopeForEntry: vi.fn(),
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

async function mountNewChat({
  locale = 'en',
  agentModelReady = true,
  gatewayResponse = [],
  sourceReady = true,
  snapshotReady = true,
  scopeReady = true,
  selectionStatus = scopeReady ? 'ready' : 'idle',
  selectionReasons = [],
  selectionLimits = { max_files: -1, max_bytes: -1 },
  organizationCapacity = {
    applicable: true,
    used_bytes: 0,
    limit_bytes: -1,
    remaining_bytes: null,
    after_create_bytes: null,
  },
}: MountScenario = {}): Promise<VueWrapper> {
  mocks.useKnowledgeSourceForm.mockReturnValue(formState({
    sourceReady,
    snapshotReady,
    scopeReady,
  }))
  mocks.useCopilotSelectionPreview.mockReturnValue({
    admission: ref({
      gateway_scope: 'platform',
      selection: { file_count: 4, size_bytes: 1024 },
      selection_limits: selectionLimits,
      organization_capacity: organizationCapacity,
      admission: { allowed: selectionReasons.length === 0, reasons: selectionReasons },
    }),
    admissionError: ref(''),
    admissionLoading: ref(false),
    calculationStatus: ref(selectionStatus),
    ready: ref(scopeReady && selectionStatus === 'ready' && selectionReasons.length === 0),
    stateForScope: () => ({
      status: scopeReady ? 'ready' : 'idle',
      summary: scopeReady ? { path_type: 'dir', file_count: 4, size_bytes: 1024, skipped_special_count: 0 } : null,
      error: '',
      retryable: false,
      coveredBy: '',
    }),
    totals: ref(scopeReady ? { fileCount: 4, sizeBytes: 1024 } : null),
  })
  mocks.fetchCopilotReadiness.mockResolvedValue({
    active_models: [],
    default_agent_model_ref: agentModelReady ? 'agent-model-ref' : null,
    default_multimodal_model_ref: 'multimodal-model-ref',
  })
  mocks.listCopilotGatewayOptions.mockReturnValue(Promise.resolve(gatewayResponse))

  const i18n = createI18n({
    legacy: false,
    locale,
    messages: { en, 'zh-hans': zhHans },
    missingWarn: false,
    fallbackWarn: false,
  })
  const wrapper = mount(NewCopilotChat, {
    global: {
      plugins: [i18n, ElementPlus],
      stubs: {
        HflPopover: HflPopoverStub,
      },
    },
  })
  await flushPromises()
  await nextTick()
  return wrapper
}

function footerHint(wrapper: VueWrapper) {
  return wrapper.find('.fullscreen-form-footer .form-submit-hint')
}

function startChatButton(wrapper: VueWrapper) {
  return wrapper.get('.fullscreen-form-footer .el-button--primary')
}

describe('New Chat Public Data Gateway warning', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      unobserve() {}
      disconnect() {}
    })
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: vi.fn(),
    })
    mocks.routerResolve.mockReturnValue({ href: '/node/nodes/deploy?role=gateway' })
    mocks.routerPush.mockResolvedValue(undefined)
    mocks.routerReplace.mockResolvedValue(undefined)
  })

  afterEach(() => {
    document.body.innerHTML = ''
    vi.unstubAllGlobals()
  })

  it('does not announce an unavailable Gateway before the first request resolves', async () => {
    const gatewayRequest = deferred<LensCopilotGatewayOption[]>()
    const wrapper = await mountNewChat({ gatewayResponse: gatewayRequest.promise })

    expect(wrapper.find('.new-chat-gateway-warning').exists()).toBe(false)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)

    gatewayRequest.resolve([])
    await flushPromises()

    expect(wrapper.get('.new-chat-gateway-warning').attributes('role')).toBe('alert')
    wrapper.unmount()
  })

  it('uses the Chinese locale for the new Chat form while preserving dynamic names', async () => {
    const wrapper = await mountNewChat({ locale: 'zh-hans', gatewayResponse: [publicGateway] })

    expect(wrapper.text()).toContain(zhHans.insight.copilot.newChat)
    expect(wrapper.text()).toContain(zhHans.insight.copilot.detailsDataSource)
    expect(wrapper.text()).toContain(zhHans.insight.copilot.bindingBackupSource)
    expect(wrapper.text()).toContain(zhHans.insight.copilot.detailsFilesFolders)
    expect(wrapper.text()).toContain(zhHans.insight.copilot.advancedOptions)
    expect(wrapper.text()).toContain(zhHans.insight.copilot.dataPrivacy)
    expect(wrapper.text()).toContain(zhHans.insight.copilot.pathCountOne.replace('{count}', '1'))
    expect(wrapper.text()).toContain('public-dg-01')
    expect(wrapper.text()).not.toContain('Data Source')
    expect(wrapper.text()).not.toContain('Backup Source')
    expect(wrapper.text()).not.toContain('Advanced options')
    wrapper.unmount()
  })

  it('shows one accessible warning in Data Privacy without repeating it in the footer', async () => {
    const wrapper = await mountNewChat()
    const message = en.insight.copilot.gatewayPublicUnavailable
    const warning = wrapper.get('.new-chat-privacy-options .new-chat-gateway-warning')

    expect(warning.attributes('role')).toBe('alert')
    expect(warning.text()).toBe(message)
    expect(warning.get('svg').attributes('aria-hidden')).toBe('true')
    expect(wrapper.text().split(message)).toHaveLength(2)
    expect(footerHint(wrapper).exists()).toBe(false)
    expect(startChatButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('removes the warning when a Public Data Gateway becomes available', async () => {
    const wrapper = await mountNewChat()
    expect(wrapper.find('.new-chat-gateway-warning').exists()).toBe(true)

    mocks.listCopilotGatewayOptions.mockResolvedValue([publicGateway])
    await wrapper.get('.new-chat-gateway-select-row__refresh').trigger('click')
    await flushPromises()

    expect(wrapper.find('.new-chat-gateway-warning').exists()).toBe(false)
    expect(footerHint(wrapper).exists()).toBe(false)
    expect(startChatButton(wrapper).attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('switches to the Private Gateway blocker without retaining the Public warning', async () => {
    const wrapper = await mountNewChat({ gatewayResponse: [privateGateway] })
    expect(wrapper.find('.new-chat-gateway-warning').exists()).toBe(true)

    await wrapper.get('input[value="manual"]').setValue(true)

    expect(wrapper.find('.new-chat-gateway-warning').exists()).toBe(false)
    expect(footerHint(wrapper).text()).toBe(en.insight.copilot.gatewayPrivateRequired)
    expect(startChatButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it.each([
    {
      name: 'Agent model',
      scenario: { agentModelReady: false },
      message: 'No default Agent model is configured. Contact your administrator.',
    },
    {
      name: 'Backup Source',
      scenario: { sourceReady: false },
      message: 'Select a backup source to continue.',
    },
    {
      name: 'Snapshot',
      scenario: { snapshotReady: false },
      message: 'Select a snapshot to continue.',
    },
    {
      name: 'file or folder scope',
      scenario: { scopeReady: false },
      message: 'Select at least one file or folder to continue.',
    },
  ])('keeps the $name blocker in the footer', async ({ scenario, message }) => {
    const wrapper = await mountNewChat(scenario)

    expect(wrapper.get('.new-chat-gateway-warning').text()).toBe(
      en.insight.copilot.gatewayPublicUnavailable,
    )
    expect(footerHint(wrapper).text()).toBe(message)
    expect(startChatButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('enables Start Chat when the form and Public Data Gateway are ready', async () => {
    const wrapper = await mountNewChat({ gatewayResponse: [publicGateway] })

    expect(wrapper.find('.new-chat-gateway-warning').exists()).toBe(false)
    expect(footerHint(wrapper).exists()).toBe(false)
    expect(wrapper.text()).toContain('1 path')
    expect(wrapper.text()).not.toContain('1 paths')
    expect(startChatButton(wrapper).attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('disables Start Chat while the selected data is being calculated', async () => {
    const wrapper = await mountNewChat({
      gatewayResponse: [publicGateway],
      selectionStatus: 'calculating',
    })

    expect(wrapper.get('.new-chat-selection-summary__status').text()).toContain('Calculating')
    expect(startChatButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it.each([
    ['selection_file_limit', 'per-Chat file limit'],
    ['selection_size_limit', 'per-Chat size limit'],
    ['organization_capacity', 'organization does not have enough'],
  ])('disables Start Chat when admission reports %s', async (reason, message) => {
    const wrapper = await mountNewChat({
      gatewayResponse: [publicGateway],
      selectionReasons: [reason],
    })

    expect(wrapper.get('.new-chat-selection-summary__status').text()).toContain(message)
    expect(startChatButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('shows Unlimited for unconfigured per-Chat limits without exposing Gateway capacity', async () => {
    const wrapper = await mountNewChat({ gatewayResponse: [publicGateway] })
    const summary = wrapper.get('.new-chat-selection-summary')

    expect(summary.text().match(/Unlimited/g)).toHaveLength(4)
    expect(summary.text()).not.toContain('Gateway capacity')
    expect(summary.text()).not.toContain('Instance')
    wrapper.unmount()
  })

  it('does not label temporarily unknown organization capacity as Unlimited', async () => {
    const wrapper = await mountNewChat({
      gatewayResponse: [publicGateway],
      selectionReasons: ['organization_capacity_unavailable'],
      organizationCapacity: {
        applicable: true,
        used_bytes: 1024,
        limit_bytes: 10 * 1024,
        remaining_bytes: null,
        after_create_bytes: null,
      },
    })
    const summary = wrapper.get('.new-chat-selection-summary')

    expect(summary.text().match(/Unavailable/g)).toHaveLength(2)
    expect(summary.text()).toContain('temporarily unavailable')
    expect(startChatButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('does not present partial organization usage as an exact value', async () => {
    const wrapper = await mountNewChat({
      gatewayResponse: [publicGateway],
      selectionReasons: ['organization_capacity_unavailable'],
      organizationCapacity: {
        applicable: true,
        limit_available: true,
        used_bytes: 1024,
        limit_bytes: 10 * 1024,
        remaining_bytes: null,
        after_create_bytes: null,
        usage_incomplete: true,
      },
    })
    const summary = wrapper.get('.new-chat-selection-summary')

    expect(summary.text().match(/Unavailable/g)).toHaveLength(3)
    expect(summary.text()).not.toContain('1 KB')
    expect(startChatButton(wrapper).attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('keeps unlimited organization capacity clear when usage is incomplete', async () => {
    const wrapper = await mountNewChat({
      gatewayResponse: [publicGateway],
      organizationCapacity: {
        applicable: true,
        limit_available: true,
        used_bytes: 1024,
        limit_bytes: -1,
        remaining_bytes: null,
        after_create_bytes: null,
        usage_incomplete: true,
      },
    })
    const summary = wrapper.get('.new-chat-selection-summary')

    expect(summary.text().match(/Unlimited/g)).toHaveLength(4)
    expect(summary.text().match(/Unavailable/g)).toHaveLength(1)
    expect(startChatButton(wrapper).attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('uses the platform default Gateway as the snapshot reader in Auto mode', async () => {
    const nonDefaultGateway: LensCopilotGatewayOption = {
      ...publicGateway,
      gateway_link_id: 12,
      gateway_id: 102,
      name: 'public-dg-02',
      is_platform_default: false,
    }
    const wrapper = await mountNewChat({
      gatewayResponse: [nonDefaultGateway, publicGateway],
    })

    const options = mocks.useKnowledgeSourceForm.mock.calls[0][2]
    expect(options.snapshotGatewayLinkId.value).toBe(publicGateway.gateway_link_id)
    wrapper.unmount()
  })

  it('reuses the create request key after an uncertain transport failure', async () => {
    mocks.createCopilotSession
      .mockRejectedValueOnce(new Error('network unavailable'))
      .mockResolvedValueOnce({ id: 91 })
    const wrapper = await mountNewChat({ gatewayResponse: [publicGateway] })

    await startChatButton(wrapper).trigger('click')
    await flushPromises()
    await startChatButton(wrapper).trigger('click')
    await flushPromises()

    expect(mocks.createCopilotSession).toHaveBeenCalledTimes(2)
    const firstKey = mocks.createCopilotSession.mock.calls[0][0].idempotency_key
    const secondKey = mocks.createCopilotSession.mock.calls[1][0].idempotency_key
    expect(firstKey).toBeTruthy()
    expect(secondKey).toBe(firstKey)
    expect(mocks.routerReplace).toHaveBeenCalledWith({
      path: '/insight/copilot',
      query: { session: '91' },
    })
    wrapper.unmount()
  })

  it('keeps the warning theme-aware and safe to wrap on narrow screens', () => {
    expect(componentSource).toContain(
      'background: color-mix(in srgb, var(--color-warning) 10%, var(--color-card-bg))',
    )
    expect(componentSource).toContain(
      'border: 1px solid color-mix(in srgb, var(--color-warning) 35%, var(--color-card-bg))',
    )
    expect(componentSource).toContain('width: 100%')
    expect(componentSource).toContain('box-sizing: border-box')
    expect(componentSource).toContain('overflow-wrap: anywhere')
  })
})
