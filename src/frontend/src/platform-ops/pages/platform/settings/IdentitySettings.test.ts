// @vitest-environment jsdom
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import IdentitySettings from './IdentitySettings.vue'

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  patch: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  copy: vi.fn(),
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('../../../../lib/clipboard', () => ({ copyTextToClipboard: mocks.copy }))
vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error },
}))
vi.mock('../../../composables/useResolvedPlatformOpsSideNav', () => ({
  useResolvedPlatformOpsSideNav: () => [],
}))
vi.mock('../../../lib/platformOpsApi', () => ({
  fetchPlatformIdentitySettings: mocks.fetch,
  patchPlatformIdentitySettings: mocks.patch,
}))
vi.mock('../../../../lib/api', () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

const Input = defineComponent({
  props: ['modelValue', 'type'],
  emits: ['update:modelValue'],
  template: `<textarea v-if="type === 'textarea'" :value="modelValue"
    @input="$emit('update:modelValue', $event.target.value)" />
    <input v-else :value="modelValue" @input="$emit('update:modelValue', $event.target.value)" />`,
})
const Button = defineComponent({
  template: '<button><slot /></button>',
})

function payload(enterprise = true, allowlist: string[] = []) {
  return {
    enterprise_identity_enabled: enterprise,
    platform_ops_enabled: true, platform_ops_allowed_cidrs: [],
    turnstile_enabled: false,
    turnstile_site_key: 'key', turnstile_secret_configured: true,
    turnstile_ip_allowlist: allowlist, has_runtime_override: !!allowlist.length,
    google_oauth_redirect_uri: 'https://console.example.test/accounts/google/login/callback/',
    iam: {},
  }
}

async function render(enterprise = true, allowlist: string[] = []) {
  mocks.fetch.mockResolvedValue(payload(enterprise, allowlist))
  const wrapper = mount(IdentitySettings, {
    global: {
      directives: { loading: () => undefined },
      stubs: {
        ModulePage: { template: '<main><slot /></main>' },
        ElInput: Input, ElButton: Button,
        ElSwitch: defineComponent({
          props: ['modelValue', 'disabled'],
          emits: ['update:modelValue'],
          template: `<input type="checkbox" :checked="modelValue" :disabled="disabled"
            @change="$emit('update:modelValue', $event.target.checked)" />`,
        }),
        ElInputNumber: true, ElAlert: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('EE Turnstile IP allowlist settings', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.copy.mockResolvedValue(undefined) })

  it('renders a single-line field beneath Secret Key and saves comma-separated addresses', async () => {
    const wrapper = await render(true, ['203.0.113.10'])
    const input = wrapper.get('#turnstile-ip-allowlist')
    expect((input.element as HTMLTextAreaElement).value).toBe('203.0.113.10')
    const turnstile = input.element.closest('section')!
    expect(turnstile.textContent).toContain('platformOps.settings.turnstileTitle')
    expect(turnstile.textContent!.indexOf('identity.turnstileSecret'))
      .toBeLessThan(turnstile.textContent!.indexOf('turnstile.ipAllowlist'))
    expect(input.element.tagName).toBe('INPUT')
    await input.setValue('203.0.113.20, , 2001:db8::1, ')
    mocks.patch.mockResolvedValue(payload(true, ['203.0.113.20', '2001:db8::1']))
    await wrapper.findAll('button').at(-1)!.trigger('click')
    await flushPromises()
    expect(mocks.patch).toHaveBeenCalledWith(expect.objectContaining({
      turnstile_ip_allowlist: ['203.0.113.20', '2001:db8::1'],
    }))
    expect((input.element as HTMLTextAreaElement).value).toBe('203.0.113.20, 2001:db8::1')
    wrapper.unmount()
  })

  it('clears the list and restores defaults using the existing buttons', async () => {
    const wrapper = await render(true, ['203.0.113.10'])
    await wrapper.get('#turnstile-ip-allowlist').setValue('')
    mocks.patch.mockResolvedValue(payload(true))
    await wrapper.findAll('button').at(-1)!.trigger('click')
    await flushPromises()
    expect(mocks.patch).toHaveBeenLastCalledWith(expect.objectContaining({
      turnstile_ip_allowlist: [],
    }))
    wrapper.unmount()
    const restored = await render(true, ['203.0.113.10'])
    mocks.patch.mockResolvedValue(payload(true))
    await restored.find('.platform-settings__actions').findAll('button')[0]!.trigger('click')
    await flushPromises()
    expect(mocks.patch).toHaveBeenLastCalledWith({ clear_runtime: true })
    expect((restored.get('#turnstile-ip-allowlist').element as HTMLTextAreaElement).value).toBe('')
    restored.unmount()
  })

  it('does not expose or submit the setting on Community', async () => {
    const wrapper = await render(false)
    expect(wrapper.find('#turnstile-ip-allowlist').exists()).toBe(false)
    mocks.patch.mockResolvedValue(payload(false))
    await wrapper.findAll('button').at(-1)!.trigger('click')
    await flushPromises()
    expect(mocks.patch).toHaveBeenCalledWith({ platform_ops_allowed_cidrs: '' })
    wrapper.unmount()
  })
})


describe('Authentication Redirect URI copy', () => {
  beforeEach(() => { vi.clearAllMocks(); mocks.copy.mockResolvedValue(undefined) })

  it('copies the configured redirect URI and reports success without saving settings', async () => {
    const wrapper = await render(true)
    try {
      const button = wrapper.get('.platform-settings__redirect-control button')
      expect(button.classes()).toContain('platform-settings__redirect-copy')
      expect(button.attributes('aria-label')).toBe('common.copy')
      expect(button.attributes('title')).toBeUndefined()
      expect(button.text()).toBe('')
      expect(button.get('svg').attributes('width')).toBe('13')
      await button.trigger('click')
      await flushPromises()
      expect(mocks.copy).toHaveBeenCalledWith('https://console.example.test/accounts/google/login/callback/')
      expect(mocks.success).toHaveBeenCalledWith({ message: 'common.copied', grouping: true })
      expect(mocks.patch).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('reports clipboard failure without changing the redirect URI', async () => {
    const wrapper = await render(true)
    mocks.copy.mockRejectedValueOnce(new Error('Clipboard unavailable'))
    try {
      await wrapper.get('.platform-settings__redirect-control button').trigger('click')
      await flushPromises()
      expect(mocks.error).toHaveBeenCalledWith({ message: 'nodesDeploy.copyFailed', grouping: true })
      expect(mocks.patch).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('disables copying when the redirect URI is missing', async () => {
    mocks.fetch.mockResolvedValueOnce({ ...payload(true), google_oauth_redirect_uri: '' })
    const wrapper = await render(true)
    try {
      const button = wrapper.get('.platform-settings__redirect-control button')
      expect((button.element as HTMLButtonElement).disabled).toBe(true)
      await button.trigger('click')
      expect(mocks.copy).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })

  it('does not expose the Google OAuth copy action on Community', async () => {
    const wrapper = await render(false)
    try {
      expect(wrapper.find('.platform-settings__redirect-control').exists()).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
})


describe('Authentication runtime Turnstile switch', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('saves the editable switch as a boolean and keeps blank secrets out of the request', async () => {
    const wrapper = await render(true)
    try {
      const row = wrapper.findAll('.platform-settings__setting-row').find(
        item => item.text().includes('platformOps.settings.turnstile.enabled'),
      )!
      const input = row.get('input[type="checkbox"]')
      expect((input.element as HTMLInputElement).disabled).toBe(false)
      await input.setValue(true)
      mocks.patch.mockResolvedValue({ ...payload(true), turnstile_enabled: true })
      await wrapper.find('.platform-settings__actions').findAll('button').at(-1)!.trigger('click')
      await flushPromises()
      expect(mocks.patch).toHaveBeenCalledWith(expect.objectContaining({ turnstile_enabled: true }))
      expect(mocks.patch.mock.calls[0]![0]).not.toHaveProperty('turnstile_secret_key')
      expect((input.element as HTMLInputElement).checked).toBe(true)
    } finally {
      wrapper.unmount()
    }
  })

  it('loads an enabled flag and can save an explicit false override', async () => {
    mocks.fetch.mockResolvedValueOnce({ ...payload(true), turnstile_enabled: true })
    const wrapper = await render(true)
    try {
      const row = wrapper.findAll('.platform-settings__setting-row').find(
        item => item.text().includes('platformOps.settings.turnstile.enabled'),
      )!
      const input = row.get('input[type="checkbox"]')
      expect((input.element as HTMLInputElement).checked).toBe(true)
      await input.setValue(false)
      mocks.patch.mockResolvedValue(payload(true))
      await wrapper.find('.platform-settings__actions').findAll('button').at(-1)!.trigger('click')
      await flushPromises()
      expect(mocks.patch).toHaveBeenCalledWith(expect.objectContaining({ turnstile_enabled: false }))
      expect((input.element as HTMLInputElement).checked).toBe(false)
    } finally {
      wrapper.unmount()
    }
  })
})


describe('Exempt IP single-line paste', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('converts multiline IPv4/IPv6 clipboard input into comma-separated addresses before the browser strips newlines', async () => {
    const wrapper = await render(true, ['203.0.113.10'])
    try {
      const input = wrapper.get('#turnstile-ip-allowlist').element as HTMLInputElement
      input.setSelectionRange(0, input.value.length)
      const event = new Event('paste', { bubbles: true, cancelable: true })
      Object.defineProperty(event, 'clipboardData', {
        value: { getData: () => '198.51.100.20\r\n\r\n 2001:db8::2, 203.0.113.30\n' },
      })
      input.dispatchEvent(event)
      await flushPromises()
      expect(event.defaultPrevented).toBe(true)
      expect(input.value).toBe('198.51.100.20, 2001:db8::2, 203.0.113.30')
      mocks.patch.mockResolvedValue(payload(true, ['198.51.100.20', '2001:db8::2', '203.0.113.30']))
      await wrapper.find('.platform-settings__actions').findAll('button').at(-1)!.trigger('click')
      await flushPromises()
      expect(mocks.patch).toHaveBeenCalledWith(expect.objectContaining({
        turnstile_ip_allowlist: ['198.51.100.20', '2001:db8::2', '203.0.113.30'],
      }))
    } finally {
      wrapper.unmount()
    }
  })

  it('preserves surrounding addresses when inserting a multiline list at the caret', async () => {
    const wrapper = await render(true, ['203.0.113.10', '2001:db8::3'])
    try {
      const input = wrapper.get('#turnstile-ip-allowlist').element as HTMLInputElement
      const position = input.value.indexOf('2001:')
      input.setSelectionRange(position, position)
      const event = new Event('paste', { bubbles: true, cancelable: true })
      Object.defineProperty(event, 'clipboardData', {
        value: { getData: () => '198.51.100.20\n2001:db8::2' },
      })
      input.dispatchEvent(event)
      await flushPromises()
      expect(input.value).toBe('203.0.113.10, 198.51.100.20, 2001:db8::2, 2001:db8::3')
    } finally {
      wrapper.unmount()
    }
  })

  it('leaves ordinary single-line paste to the browser', async () => {
    const wrapper = await render(true)
    try {
      const input = wrapper.get('#turnstile-ip-allowlist').element as HTMLInputElement
      const event = new Event('paste', { bubbles: true, cancelable: true })
      Object.defineProperty(event, 'clipboardData', {
        value: { getData: () => '203.0.113.10' },
      })
      input.dispatchEvent(event)
      await flushPromises()
      expect(event.defaultPrevented).toBe(false)
      expect(mocks.patch).not.toHaveBeenCalled()
    } finally {
      wrapper.unmount()
    }
  })
})
