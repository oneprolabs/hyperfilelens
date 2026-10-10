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
}))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
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
    turnstile_site_key: 'key', turnstile_secret_configured: true,
    turnstile_ip_allowlist: allowlist, has_runtime_override: !!allowlist.length,
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
        ElInput: Input, ElButton: Button, ElSwitch: true,
        ElInputNumber: true, ElAlert: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('EE Turnstile IP allowlist settings', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('renders beneath Secret Key and saves one address per line', async () => {
    const wrapper = await render(true, ['203.0.113.10'])
    const input = wrapper.get('#turnstile-ip-allowlist')
    expect((input.element as HTMLTextAreaElement).value).toBe('203.0.113.10')
    const turnstile = input.element.closest('section')!
    expect(turnstile.textContent).toContain('platformOps.settings.turnstileTitle')
    expect(turnstile.textContent!.indexOf('identity.turnstileSecret'))
      .toBeLessThan(turnstile.textContent!.indexOf('turnstile.ipAllowlist'))
    await input.setValue('203.0.113.20\n\n 2001:db8::1 \n')
    mocks.patch.mockResolvedValue(payload(true, ['203.0.113.20', '2001:db8::1']))
    await wrapper.findAll('button').at(-1)!.trigger('click')
    await flushPromises()
    expect(mocks.patch).toHaveBeenCalledWith(expect.objectContaining({
      turnstile_ip_allowlist: ['203.0.113.20', '2001:db8::1'],
    }))
    expect((input.element as HTMLTextAreaElement).value).toBe('203.0.113.20\n2001:db8::1')
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
    await restored.findAll('button')[0]!.trigger('click')
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
