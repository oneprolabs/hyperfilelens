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
    expect(wrapper.text()).toContain('Manual override')
    expect(wrapper.text()).toMatch(/4\s+\/ 10/)
    wrapper.unmount()
  })

  it('hides instance activation from tenant users without platform permission', async () => {
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
    expect(wrapper.text()).toContain('deployment instance license')
    wrapper.unmount()
  })

  it('shows an installed inactive Enterprise license as inactive', async () => {
    mocks.fetchCurrentLicense.mockResolvedValue({
      is_valid: false,
      enforcement_enabled: true,
      entitlement_source: 'license_inactive',
      instance_shared: false,
      can_manage_instance_license: false,
      limits: { max_users: -1 },
      usage: { users_count: 1 },
      license: {
        id: 'inactive-license',
        license_key: 'inactive-key',
        is_valid: false,
        status: 'revoked',
      },
    })

    const wrapper = await mountSubscription()

    expect(wrapper.text()).toContain('Enterprise')
    expect(wrapper.text()).toContain('Inactive')
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
    wrapper.unmount()
  })

  it('keeps the license overview when license history is unavailable', async () => {
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    mocks.fetchCurrentLicense.mockResolvedValue({
      is_valid: true,
      enforcement_enabled: true,
      instance_shared: true,
      limits: { max_users: 10 },
      usage: { users_count: 1 },
    })
    mocks.fetchLicenseHistory.mockRejectedValue(new Error('history unavailable'))

    const wrapper = await mountSubscription()

    expect(wrapper.text()).toContain('Enterprise')
    expect(wrapper.text()).toContain('License history is temporarily unavailable.')
    expect(errorMessage).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps the license overview and disables activation when identification loading fails', async () => {
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    mocks.fetchCurrentLicense.mockResolvedValue({
      is_valid: true,
      enforcement_enabled: true,
      instance_shared: false,
      can_manage_instance_license: true,
      limits: { max_users: 10 },
      usage: { users_count: 1 },
    })
    mocks.fetchMachineCode.mockRejectedValue(new Error('machine code unavailable'))

    const wrapper = await mountSubscription()

    expect(wrapper.text()).toContain('Enterprise')
    expect(wrapper.text()).toContain(
      'Unable to load the identification code. Refresh the page and try again.',
    )
    const activateButton = wrapper.findAll('button').find((button) => (
      button.text().includes('Activate License')
    ))
    expect(activateButton?.attributes('disabled')).toBeDefined()
    expect(errorMessage).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps activation failures separate from license-loading failures', async () => {
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    mocks.fetchCurrentLicense.mockResolvedValue({
      is_valid: true,
      enforcement_enabled: true,
      instance_shared: false,
      can_manage_instance_license: true,
      machine_code: 'machine-code',
      limits: { max_users: 10 },
      usage: { users_count: 1 },
    })
    mocks.activateLicense.mockRejectedValue(new Error('Activation code expired'))

    const wrapper = await mountSubscription()
    await wrapper.get('textarea').setValue('HFL-ACT-test')
    const activateButton = wrapper.findAll('button').find((button) => (
      button.text().includes('Activate License')
    ))
    expect(activateButton).toBeDefined()
    await activateButton!.trigger('click')
    await flushPromises()

    expect(errorMessage).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Activation code expired',
    }))
    wrapper.unmount()
  })

  it('uses the activation fallback only for an activation request without a server message', async () => {
    const errorMessage = vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    mocks.fetchCurrentLicense.mockResolvedValue({
      is_valid: true,
      enforcement_enabled: true,
      instance_shared: false,
      can_manage_instance_license: true,
      machine_code: 'machine-code',
      limits: { max_users: 10 },
      usage: { users_count: 1 },
    })
    mocks.activateLicense.mockRejectedValue({})

    const wrapper = await mountSubscription()
    await wrapper.get('textarea').setValue('HFL-ACT-test')
    const activateButton = wrapper.findAll('button').find((button) => (
      button.text().includes('Activate License')
    ))
    await activateButton!.trigger('click')
    await flushPromises()

    expect(errorMessage).toHaveBeenCalledWith(expect.objectContaining({
      message: 'Activation failed',
    }))
    wrapper.unmount()
  })
})
