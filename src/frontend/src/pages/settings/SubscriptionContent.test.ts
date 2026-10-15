// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessage } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import SubscriptionContent from './SubscriptionContent.vue'

const mocks = vi.hoisted(() => ({
  fetchCurrentLicense: vi.fn(),
  fetchEffectiveQuotaUsage: vi.fn(),
  fetchLicenseHistory: vi.fn(),
  fetchMachineCode: vi.fn(),
  activateLicense: vi.fn(),
}))

vi.mock('../../lib/subscriptionApi', () => mocks)

async function mountSubscription() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  const wrapper = mount(SubscriptionContent, {
    global: {
      plugins: [ElementPlus, i18n],
      directives: {
        tableColumnResize: {},
      },
      stubs: {
        HflTablePanel: {
          template: '<div><slot /></div>',
        },
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('SubscriptionContent effective quotas', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchLicenseHistory.mockResolvedValue([])
    mocks.fetchMachineCode.mockResolvedValue({ machine_code: 'machine-code' })
    mocks.fetchEffectiveQuotaUsage.mockResolvedValue({
      organization_id: 7,
      organization_key: 'tenant-seven',
      quota_usage: [],
    })
  })

  it('does not call the Enterprise-only endpoint in Community mode', async () => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      is_valid: false,
      enforcement_enabled: false,
      instance_shared: false,
      machine_code: 'machine-code',
      limits: {},
      usage: {},
    })

    const wrapper = await mountSubscription()

    expect(mocks.fetchEffectiveQuotaUsage).not.toHaveBeenCalled()
    expect(wrapper.text()).toMatch(/0\s+\/ 1/)
    expect(wrapper.text()).toMatch(/0\s+\/ 100/)
    expect(wrapper.text()).toContain('0 MB / 100 TB')
    expect(wrapper.text()).toContain('Unlimited')
    wrapper.unmount()
  })

  it('loads authoritative effective quotas when Enterprise enforcement is active', async () => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      is_valid: true,
      enforcement_enabled: true,
      instance_shared: true,
      machine_code: 'machine-code',
      limits: { max_users: 10 },
      usage: { users_count: 3 },
      license: {
        id: 'license-seven',
        license_key: 'license-key-seven',
        is_valid: true,
        status: 'active',
      },
    })
    mocks.fetchEffectiveQuotaUsage.mockResolvedValue({
      organization_id: 7,
      organization_key: 'tenant-seven',
      quota_usage: [
        {
          key: 'max_users',
          limit: 10,
          unit: 'count',
          used: 4,
          plan_key: 'pro',
          plan_limit: 50,
          override_limit: 10,
          limit_source: 'override',
          overridden: true,
          remaining: 6,
          usage_percent: 40,
          usage_status: 'ok',
        },
      ],
    })

    const wrapper = await mountSubscription()

    expect(mocks.fetchEffectiveQuotaUsage).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('Members')
    expect(wrapper.text()).toMatch(/4\s+\/ 10/)
    wrapper.unmount()
  })

  it('uses a warning state and explicit text when a quota is exceeded', async () => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      is_valid: true,
      enforcement_enabled: true,
      instance_shared: true,
      limits: { max_users: 1 },
      usage: { users_count: 11 },
    })
    mocks.fetchEffectiveQuotaUsage.mockResolvedValue({
      organization_id: 7,
      organization_key: 'tenant-seven',
      quota_usage: [{
        key: 'max_users',
        limit: 1,
        unit: 'count',
        used: 11,
        usage_percent: 110,
        usage_status: 'exceeded',
      }],
    })

    const wrapper = await mountSubscription()

    expect(wrapper.text()).toContain('Over Limit')
    expect(wrapper.find('.subscription-quota-item__progress.is-over-limit').exists()).toBe(true)
    expect(wrapper.find('.el-progress--exception').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps unlimited usage and status on separate rows without a percentage', async () => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      entitlement_source: 'builtin_community',
      limits: { ai_tokens: -1 },
      usage: { ai_tokens_used: 537099 },
    })

    const wrapper = await mountSubscription()
    const card = wrapper.findAll('.subscription-quota-item')
      .find((item) => item.find('.subscription-quota-item__label').text() === 'AI Usage')!

    expect(card.find('.subscription-quota-item__numbers').text()).toBe('537.1K tokens')
    expect(card.find('.subscription-quota-item__numbers').attributes('title')).toBeUndefined()
    expect(card.find('.subscription-quota-item__status').text()).toBe('Unlimited')
    expect(card.find('.subscription-quota-item__track').attributes('aria-hidden')).toBe('true')
    expect(card.find('.el-progress__text').exists()).toBe(false)
    expect(card.find('.subscription-quota-item__status').element.parentElement)
      .toBe(card.find('.subscription-quota-item__progress').element)
    wrapper.unmount()
  })

  it.each([
    [0, '0'],
    [999, '999'],
    [1000, '1K'],
    [12500, '12.5K'],
    [537099, '537.1K'],
    [1250000, '1.25M'],
    [1000000000, '1B'],
  ])('formats %s tokens compactly as %s without changing resource counts', async (used, display) => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      entitlement_source: 'builtin_community',
      limits: { ai_tokens: -1 },
      usage: { ai_tokens_used: used, agents_count: 12500 },
    })
    const wrapper = await mountSubscription()
    const cards = wrapper.findAll('.subscription-quota-item')
    const ai = cards.find((item) => item.text().includes('AI Usage'))!
    const hosts = cards.find((item) => item.text().includes('Source Hosts'))!

    expect(ai.find('.subscription-quota-item__numbers').text()).toBe(`${display} tokens`)
    expect(hosts.find('.subscription-quota-item__numbers').text()).toBe('12,500')
    wrapper.unmount()
  })

  it('formats token usage and limit with a single unit while keeping the raw percentage', async () => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      entitlement_source: 'builtin_community',
      limits: { ai_tokens: 1000000 },
      usage: { ai_tokens_used: 537099 },
    })
    const wrapper = await mountSubscription()
    const ai = wrapper.findAll('.subscription-quota-item')
      .find((item) => item.text().includes('AI Usage'))!

    expect(ai.find('.subscription-quota-item__numbers').text()).toBe('537.1K / 1M tokens')
    expect(ai.find('.subscription-quota-item__status').text()).toBe('54%')
    expect(Number(ai.find('.subscription-quota-item__track').attributes('aria-valuenow')))
      .toBeCloseTo(53.7099, 4)
    expect(ai.find('.subscription-quota-item__numbers').attributes('title')).toBeUndefined()
    expect(ai.find('.el-tooltip__trigger').exists()).toBe(false)
    wrapper.unmount()
  })

  it('distinguishes very small nonzero usage from zero without inflating the fill', async () => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      entitlement_source: 'builtin_community',
      limits: { max_storage_bytes: 100 * 1024 ** 4, max_protected_sources: 100 },
      usage: { storage_used_bytes: 6.3 * 1024 ** 2, protected_sources_count: 4 },
    })

    const wrapper = await mountSubscription()
    const cards = wrapper.findAll('.subscription-quota-item')
    const storage = cards.find((item) => item.text().includes('Backup Storage'))!
    const backups = cards.find((item) => item.text().includes('Backup Configurations'))!
    const members = cards.find((item) => item.text().includes('Members'))!

    expect(storage.find('.subscription-quota-item__numbers').text()).toBe('6.3 MB / 100 TB')
    expect(storage.find('.subscription-quota-item__status').text()).toBe('<0.1%')
    const fill = Number.parseFloat(storage.find('.el-progress-bar__inner').element.getAttribute('style')!.match(/width: ([\d.e+-]+)%/)![1])
    expect(fill).toBeGreaterThan(0)
    expect(fill).toBeLessThan(0.1)
    expect(backups.find('.subscription-quota-item__status').text()).toBe('4%')
    expect(members.find('.subscription-quota-item__status').text()).toBe('0%')
    wrapper.unmount()
  })

  it('shows unavailable usage instead of implying a zero percentage', async () => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      enforcement_enabled: true,
      instance_shared: true,
      limits: { max_storage_bytes: 100 * 1024 ** 4 },
      usage: {},
    })
    mocks.fetchEffectiveQuotaUsage.mockResolvedValue({
      quota_usage: [{
        key: 'max_storage_bytes',
        limit: 100 * 1024 ** 4,
        used: 0,
        usage_status: 'unknown',
        usage_percent: null,
      }],
    })

    const wrapper = await mountSubscription()
    const storage = wrapper.findAll('.subscription-quota-item')
      .find((item) => item.text().includes('Backup Storage'))!

    expect(storage.find('.subscription-quota-item__status').text()).toBe('Usage unavailable')
    expect(storage.find('.subscription-quota-item__track').attributes('aria-hidden')).toBe('true')
    wrapper.unmount()
  })

  it('shares only the widest rendered status width and remeasures font changes', async () => {
    let widestWidth = 62
    let resized = () => {}
    const disconnect = vi.fn()
    const observe = vi.fn()
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { resized = callback }
      disconnect = disconnect
      observe = observe
    })
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const width = this.classList.contains('subscription-quota-item__status-text')
        ? this.textContent === 'Over Limit' ? widestWidth : 54
        : 0
      return new DOMRect(0, 0, width, 12)
    })
    mocks.fetchCurrentLicense.mockResolvedValue({
      entitlement_source: 'builtin_community',
      limits: { max_users: 1 },
      usage: { users_count: 11 },
    })

    const wrapper = await mountSubscription()
    expect(wrapper.find('.subscription-quota-grid').attributes('style'))
      .toContain('--quota-status-width: 62px')
    expect(observe).toHaveBeenCalledTimes(wrapper.findAll('.subscription-quota-item').length)
    widestWidth = 71.4
    resized()
    await flushPromises()
    expect(wrapper.find('.subscription-quota-grid').attributes('style'))
      .toContain('--quota-status-width: 72px')
    wrapper.unmount()
    expect(disconnect).toHaveBeenCalled()
  })

  it('keeps license activation and history out of the shared tenant page', async () => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      is_valid: true,
      enforcement_enabled: true,
      instance_shared: false,
      can_manage_instance_license: false,
      limits: { max_users: -1 },
      usage: { users_count: 1 },
    })

    const wrapper = await mountSubscription()

    expect(mocks.fetchMachineCode).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('Activate License')
    expect(wrapper.text()).not.toContain('License Activation')
    expect(wrapper.text()).not.toContain('License History')
    expect(wrapper.text()).toContain('Resource Usage')
    wrapper.unmount()
  })

  it('silently ignores route cancellation while loading license information', async () => {
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    const aborted = new Error('')
    aborted.name = 'AbortError'
    mocks.fetchCurrentLicense.mockRejectedValue(aborted)

    const wrapper = await mountSubscription()

    expect(errorMessage).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('uses a license-loading message for a real current-license failure', async () => {
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    mocks.fetchCurrentLicense.mockRejectedValue(new Error(''))

    const wrapper = await mountSubscription()

    expect(errorMessage).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Unable to load license information. Refresh the page and try again.',
    }))
    expect(wrapper.text()).toContain('Quota information is temporarily unavailable.')
    expect(wrapper.text()).not.toContain('0 MB / 100 TB')
    wrapper.unmount()
  })

  it.each(['current', 'effective'])('shows access denial instead of quota unavailability after %s returns 403', async (endpoint) => {
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    mocks.fetchCurrentLicense.mockResolvedValue({
      enforcement_enabled: true, instance_shared: true,
      limits: { max_users: 10 }, usage: { users_count: 3 },
    })
    if (endpoint === 'current') mocks.fetchCurrentLicense.mockRejectedValue({ status: 403 })
    else mocks.fetchEffectiveQuotaUsage.mockRejectedValue({ status: 403 })
    const wrapper = await mountSubscription()
    expect(wrapper.text()).toContain("You do not have permission to view this organization's subscription.")
    expect(wrapper.text()).not.toContain('Quota information is temporarily unavailable.')
    expect(wrapper.text()).not.toContain('Unable to load effective organization quotas.')
    expect(wrapper.find('.subscription-quota-grid').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Retry')
    expect(errorMessage).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it.each([500, 0])('does not quietly substitute base values after effective quotas fail with %s', async (status) => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      enforcement_enabled: true, instance_shared: true,
      limits: { max_users: 10 }, usage: { users_count: 3 },
    })
    mocks.fetchEffectiveQuotaUsage.mockRejectedValue({ status })
    const wrapper = await mountSubscription()
    expect(wrapper.text()).toContain('Unable to load effective organization quotas.')
    expect(wrapper.find('.subscription-quota-grid').exists()).toBe(false)
    expect(wrapper.text()).toContain('Retry')
    mocks.fetchEffectiveQuotaUsage.mockResolvedValue({
      quota_usage: [{ key: 'max_users', limit: 10, used: 4 }],
    })
    await wrapper.findAll('button').find((button) => button.text() === 'Retry')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toMatch(/4\s+\/ 10/)
    expect(wrapper.text()).not.toContain('Unable to load effective organization quotas.')
    wrapper.unmount()
  })

  it.each([404, 405])('labels an absent older effective quota endpoint (%s) as a compatibility fallback', async (status) => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      enforcement_enabled: true, instance_shared: true,
      limits: { max_users: 10 }, usage: { users_count: 3 },
    })
    mocks.fetchEffectiveQuotaUsage.mockRejectedValue({ status })
    const wrapper = await mountSubscription()
    expect(wrapper.text()).toContain('Showing limits and usage from the base subscription API.')
    expect(wrapper.text()).toMatch(/3\s+\/ 10/)
    wrapper.unmount()
  })
})
