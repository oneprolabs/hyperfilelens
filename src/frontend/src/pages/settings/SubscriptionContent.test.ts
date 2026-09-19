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

})
