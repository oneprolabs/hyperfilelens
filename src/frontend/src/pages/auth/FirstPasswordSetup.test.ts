// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import { createI18n } from 'vue-i18n'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import FirstPasswordSetup from './FirstPasswordSetup.vue'

const mocks = vi.hoisted(() => ({ api: vi.fn(), logout: vi.fn() }))
vi.mock('../../lib/api', () => ({
  api: mocks.api, apiErrorMessageI18n: (_error: unknown, _t: unknown, fallback: string) => fallback,
}))
vi.mock('../../composables/useAuth', () => ({
  currentUser: { email: 'new@test.local' }, useAuth: () => ({ logout: mocks.logout }),
}))
const global = { plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })] }
describe('First password setup', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.api.mockResolvedValue({}); mocks.logout.mockResolvedValue(undefined) })
  it('requires matching passwords and submits the existing password change API', async () => {
    const wrapper = mount(FirstPasswordSetup, { global })
    await wrapper.find('form').trigger('submit')
    expect(mocks.api).not.toHaveBeenCalled()
    await wrapper.find('#setup-current').setValue('TemporaryA9!')
    await wrapper.find('#setup-password').setValue('PersonalA9!')
    await wrapper.find('#setup-confirm').setValue('PersonalA9!')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(mocks.api).toHaveBeenCalledWith('/api/v1/auth/change-password', {
      method: 'POST', body: JSON.stringify({
        current_password: 'TemporaryA9!', new_password: 'PersonalA9!', confirm_password: 'PersonalA9!',
      }),
    })
    expect(mocks.logout).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
  it('keeps setup open when the backend rejects the password', async () => {
    const wrapper = mount(FirstPasswordSetup, { global })
    await flushPromises()
    await wrapper.find('#setup-current').setValue('TemporaryA9!')
    await wrapper.find('#setup-password').setValue('PersonalA9!')
    await wrapper.find('#setup-confirm').setValue('PersonalA9!')
    mocks.api.mockRejectedValueOnce(new Error('Rejected'))
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(mocks.logout).not.toHaveBeenCalled()
    expect(wrapper.find('.el-alert').exists()).toBe(true)
    wrapper.unmount()
  })
})
