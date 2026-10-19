// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EmailSettings from './EmailSettings.vue'

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(), patch: vi.fn(), send: vi.fn(),
  success: vi.fn(), error: vi.fn(), warning: vi.fn(),
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => {
  const labels: Record<string, string> = {
    'platformOps.settings.email.testSend': 'Send',
    'platformOps.settings.email.testSending': 'Sending…',
    'platformOps.settings.email.testSavedSettingsHint': 'Uses saved email settings.',
  }
  return labels[key] || key
} }) }))
vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error, warning: mocks.warning },
}))
vi.mock('../../../composables/useResolvedPlatformOpsSideNav', () => ({ useResolvedPlatformOpsSideNav: () => [] }))
vi.mock('../../../lib/platformOpsApi', () => ({
  fetchPlatformEmailSettings: mocks.fetch,
  patchPlatformEmailSettings: mocks.patch,
  testPlatformEmail: mocks.send,
}))
vi.mock('../../../../lib/api', () => ({ apiErrorMessage: (_error: unknown, fallback: string) => fallback }))

const Input = defineComponent({
  props: ['modelValue', 'disabled'], emits: ['update:modelValue'],
  template: '<input :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', $event.target.value)" />',
})
const Button = defineComponent({
  props: ['disabled', 'loading', 'type'],
  template: '<button :disabled="disabled || loading" :data-loading="loading" :data-type="type"><slot /></button>',
})
const config = {
  enterprise_identity_enabled: true, has_runtime_override: false, source: 'deployment',
  delivery_configured: true, configuration_error: '', backend: 'smtp',
  host: 'smtp.example.test', port: 587, host_user: 'mail-user', from_email: 'sender@example.test',
  password_configured: true, use_tls: true, use_ssl: false,
}
async function render() {
  const wrapper = mount(EmailSettings, { global: {
    directives: { loading: () => undefined },
    stubs: {
      ModulePage: { template: '<main><slot /></main>' }, ElInput: Input, ElButton: Button,
      ElInputNumber: true, ElSelect: true, ElOption: true, ElAlert: true,
    },
  } })
  await flushPromises()
  return wrapper
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes })
  return { promise, resolve }
}

describe('Send Test Email action', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.fetch.mockResolvedValue(config) })

  it('keeps the secondary Send button icon-free, with no hover title, and disabled for an empty recipient', async () => {
    const wrapper = await render()
    try {
      const button = wrapper.get('.platform-settings__test-send')
      expect(button.text()).toBe('Send')
      expect(button.attributes('data-type')).toBeUndefined()
      expect(button.attributes('title')).toBeUndefined()
      expect(button.find('svg').exists()).toBe(false)
      expect((button.element as HTMLButtonElement).disabled).toBe(true)
      expect(wrapper.get('#test-email-settings-hint').text()).toBe('Uses saved email settings.')
      await wrapper.get('#test-email-recipient').setValue('   ')
      expect((button.element as HTMLButtonElement).disabled).toBe(true)
      await button.trigger('click')
      expect(mocks.send).not.toHaveBeenCalled()
    } finally { wrapper.unmount() }
  })

  it('shows Sending while pending, prevents duplicate sends and restores Send afterwards', async () => {
    const request = deferred<{ ok: boolean; recipient: string }>()
    mocks.send.mockReturnValue(request.promise)
    const wrapper = await render()
    try {
      await wrapper.get('#test-email-recipient').setValue('  ops@example.test  ')
      const button = wrapper.get('.platform-settings__test-send')
      await button.trigger('click')
      expect(mocks.send).toHaveBeenCalledWith('ops@example.test')
      expect(button.text()).toBe('Sending…')
      expect(button.attributes('data-loading')).toBe('true')
      expect((button.element as HTMLButtonElement).disabled).toBe(true)
      expect((wrapper.get('#test-email-recipient').element as HTMLInputElement).disabled).toBe(true)
      await button.trigger('click')
      expect(mocks.send).toHaveBeenCalledTimes(1)
      request.resolve({ ok: true, recipient: 'ops@example.test' })
      await flushPromises()
      expect(button.text()).toBe('Send')
      expect((button.element as HTMLButtonElement).disabled).toBe(false)
      expect(mocks.success).toHaveBeenCalled()
      expect(mocks.patch).not.toHaveBeenCalled()
    } finally { wrapper.unmount() }
  })

  it('disables sending while SMTP settings are being saved', async () => {
    const request = deferred<typeof config>()
    mocks.patch.mockReturnValue(request.promise)
    const wrapper = await render()
    try {
      await wrapper.get('#test-email-recipient').setValue('ops@example.test')
      await wrapper.find('.platform-settings__actions').findAll('button').at(-1)!.trigger('click')
      const button = wrapper.get('.platform-settings__test-send')
      expect((button.element as HTMLButtonElement).disabled).toBe(true)
      await button.trigger('click')
      expect(mocks.send).not.toHaveBeenCalled()
      request.resolve(config)
      await flushPromises()
      expect((button.element as HTMLButtonElement).disabled).toBe(false)
    } finally { wrapper.unmount() }
  })

  it('does not send or save SMTP drafts when testing a recipient', async () => {
    mocks.send.mockResolvedValue({ ok: true })
    const wrapper = await render()
    try {
      await wrapper.findAll('input')[0]!.setValue('unsaved.example.test')
      await wrapper.get('#test-email-recipient').setValue('ops@example.test')
      await wrapper.get('.platform-settings__test-send').trigger('click')
      await flushPromises()
      expect(mocks.send).toHaveBeenCalledWith('ops@example.test')
      expect(mocks.patch).not.toHaveBeenCalled()
    } finally { wrapper.unmount() }
  })

  it('restores the action after a failed request and keeps the existing error feedback', async () => {
    mocks.send.mockRejectedValue(new Error('Test failure'))
    const wrapper = await render()
    try {
      await wrapper.get('#test-email-recipient').setValue('ops@example.test')
      const button = wrapper.get('.platform-settings__test-send')
      await button.trigger('click')
      await flushPromises()
      expect(button.text()).toBe('Send')
      expect((button.element as HTMLButtonElement).disabled).toBe(false)
      expect(mocks.error).toHaveBeenCalledWith({ message: 'platformOps.settings.email.testFailed', grouping: true })
    } finally { wrapper.unmount() }
  })
})
