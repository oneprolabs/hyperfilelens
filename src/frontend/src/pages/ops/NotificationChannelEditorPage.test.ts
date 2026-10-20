// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElMessageBox } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import NotificationChannelEditorPage from './NotificationChannelEditorPage.vue'

const mocks = vi.hoisted(() => ({
  getChannel: vi.fn(), updateChannel: vi.fn(), testChannelConfig: vi.fn(), push: vi.fn(),
}))
vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: '7' } }),
  useRouter: () => ({ push: mocks.push }),
}))
vi.mock('../../lib/api', () => ({ apiErrorMessageI18n: () => 'Request failed' }))
vi.mock('../../lib/notificationApi', () => ({
  getChannel: mocks.getChannel,
  updateChannel: mocks.updateChannel,
  testChannelConfig: mocks.testChannelConfig,
  createChannel: vi.fn(),
}))

const wrappers: ReturnType<typeof mount>[] = []
async function render() {
  const wrapper = mount(NotificationChannelEditorPage, {
    global: { plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
function button(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find(item => item.text() === text)!
}

describe('Webhook signing secret editor', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
    mocks.getChannel.mockResolvedValue({
      id: 7, name: 'Feishu test', type: 'webhook', enabled: true,
      config: { url: 'https://open.feishu.cn/open-apis/bot/v2/hook/test', secret: '********' },
    })
    mocks.updateChannel.mockResolvedValue({ id: 7 })
    mocks.testChannelConfig.mockResolvedValue({ status: 'success' })
  })
  afterEach(() => {
    wrappers.splice(0).forEach(wrapper => wrapper.unmount())
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('masks the saved key, cancels edits, and saves without replacing the existing key', async () => {
    const wrapper = await render()
    expect(wrapper.get('input[type="password"]').attributes('disabled')).toBeDefined()
    await button(wrapper, en.ops.notification.rewriteSecret).trigger('click')
    await wrapper.get('input[type="password"]').setValue('replacement-secret')
    expect(wrapper.get('.add-form-preview-body').text()).not.toContain('replacement-secret')
    await button(wrapper, en.common.cancel).trigger('click')
    await button(wrapper, en.common.save).trigger('click')
    await flushPromises()
    expect(mocks.updateChannel).toHaveBeenCalledWith(7, expect.objectContaining({
      config: expect.not.objectContaining({ secret: expect.anything() }),
    }))
    expect(mocks.updateChannel.mock.calls[0][1].config).not.toHaveProperty('clear_secret')
  })

  it('tests a replacement without saving and prevents duplicate sends while pending', async () => {
    let finish!: (result: { status: string }) => void
    mocks.testChannelConfig.mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const wrapper = await render()
    await button(wrapper, en.ops.notification.rewriteSecret).trigger('click')
    await wrapper.get('input[type="password"]').setValue('replacement-secret')
    const test = button(wrapper, en.ops.notification.testWebhookDraft)
    await test.trigger('click')
    await flushPromises()
    expect(test.attributes('disabled')).toBeDefined()
    await test.trigger('click')
    expect(mocks.testChannelConfig).toHaveBeenCalledTimes(1)
    expect(mocks.testChannelConfig).toHaveBeenCalledWith(expect.objectContaining({
      channel_id: 7, config: expect.objectContaining({ secret: 'replacement-secret' }),
    }))
    expect(mocks.updateChannel).not.toHaveBeenCalled()
    finish({ status: 'success' })
    await flushPromises()
    expect(test.attributes('disabled')).toBeUndefined()
  })

  it('requires confirmation to clear a saved key, for both testing and saving', async () => {
    const confirm = vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue('cancel')
    const wrapper = await render()
    await button(wrapper, en.ops.notification.rewriteSecret).trigger('click')
    await button(wrapper, en.ops.notification.testWebhookDraft).trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalled()
    expect(mocks.testChannelConfig).not.toHaveBeenCalled()
    confirm.mockResolvedValue('confirm')
    await button(wrapper, en.ops.notification.testWebhookDraft).trigger('click')
    await flushPromises()
    expect(mocks.testChannelConfig).toHaveBeenCalledWith(expect.objectContaining({
      config: expect.objectContaining({ clear_secret: true }),
    }))
    await button(wrapper, en.common.save).trigger('click')
    await flushPromises()
    expect(mocks.updateChannel).toHaveBeenCalledWith(7, expect.objectContaining({
      config: expect.objectContaining({ clear_secret: true }),
    }))
    expect(confirm).toHaveBeenCalledTimes(3)
  })
})
