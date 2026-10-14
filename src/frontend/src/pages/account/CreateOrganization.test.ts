// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import CreateOrganization from './CreateOrganization.vue'

const mocks = vi.hoisted(() => ({ api: vi.fn(), setOrg: vi.fn(), fetchUser: vi.fn(), back: vi.fn() }))
vi.mock('../../lib/api', () => ({
  api: mocks.api, apiErrorMessageI18n: (_error: unknown, _t: unknown, fallback: string) => fallback,
}))
vi.mock('../../composables/useAuth', () => ({
  setStoredOrgKey: mocks.setOrg, fetchCurrentUser: mocks.fetchUser,
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ back: mocks.back }) }))
const global = { plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })] }
describe('Create Organization', () => {
  it('checks account capability before enabling creation and sends only the name', async () => {
    mocks.api.mockReset()
    mocks.api.mockResolvedValueOnce({ can_create: true })
    const wrapper = mount(CreateOrganization, { global })
    await flushPromises()
    expect(wrapper.find('h1').text()).toBe('Create Organization')
    await wrapper.find('#organization-name').setValue('Independent')
    mocks.api.mockRejectedValueOnce(new Error('Quota full'))
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/iam/create-organization/', {
      method: 'POST', body: JSON.stringify({ name: 'Independent' }),
    })
    expect(wrapper.find('.el-alert').exists()).toBe(true)
    wrapper.unmount()
  })
  it('does not submit when self-service creation is disabled', async () => {
    mocks.api.mockReset()
    mocks.api.mockResolvedValueOnce({ can_create: false })
    const wrapper = mount(CreateOrganization, { global })
    await flushPromises()
    await wrapper.find('form').trigger('submit')
    expect(mocks.api).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('Organization creation is disabled')
    wrapper.unmount()
  })
})
