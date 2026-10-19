// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../../../locales/en'
import ExternalAccessSettings from './ExternalAccessSettings.vue'

const mocks = vi.hoisted(() => ({
  api: vi.fn(), profile: vi.fn(), clear: vi.fn(), error: vi.fn(), success: vi.fn(),
}))
vi.mock('../../../../lib/api', () => ({
  api: mocks.api,
  apiErrorMessage: (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback,
}))
vi.mock('element-plus', () => ({ ElMessage: { error: mocks.error, success: mocks.success } }))
vi.mock('../../../../composables/useDeployProfile', () => ({
  clearDeployProfileCache: mocks.clear, fetchDeployProfile: mocks.profile,
}))
vi.mock('../../../composables/useResolvedPlatformOpsSideNav', async () => {
  const { ref } = await import('vue')
  return { useResolvedPlatformOpsSideNav: () => ref([]) }
})

const Input = defineComponent({
  inheritAttrs: false,
  props: ['modelValue', 'disabled'],
  emits: ['update:modelValue', 'input'],
  setup(props, { attrs, emit }) {
    return () => h('input', {
      ...attrs, value: props.modelValue, disabled: props.disabled,
      onInput: (event: Event) => {
        const value = (event.target as HTMLInputElement).value
        emit('update:modelValue', value)
        emit('input', value)
      },
    })
  },
})
const Button = defineComponent({
  props: ['disabled', 'loading'],
  setup(props, { slots }) {
    return () => h('button', { disabled: props.disabled || props.loading }, slots.default?.())
  },
})
const wrappers: ReturnType<typeof mount>[] = []
let state: Record<string, unknown>
const path = '/api/v1/platform-ops/platform/settings/external-access'

function render() {
  const wrapper = mount(ExternalAccessSettings, {
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      directives: { loading: {} },
      stubs: {
        ModulePage: { template: '<main><slot /></main>' },
        ElInput: Input, ElButton: Button, ElAlert: true,
      },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}

function body() {
  const call = mocks.api.mock.calls.find(([, init]) => init?.method === 'PATCH')
  return call ? JSON.parse(call[1].body) : undefined
}
function button(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((item) => item.text() === text)!
}

describe('External Access Product Website setting', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state = {
      external_access_url: '', effective_url: 'https://agents.example:11443',
      source: 'deployment', has_runtime_override: false,
      suggested_url: 'https://192.168.8.69:11443', editable: true,
      product_website_url: '', default_product_website_url: 'https://192.168.8.69:11442',
    }
    mocks.profile.mockResolvedValue(null)
    mocks.api.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url !== path) throw new Error(`Unexpected request: ${url}`)
      if (init?.method === 'PATCH') {
        const patch = JSON.parse(init.body as string)
        if ('external_access_url' in patch) {
          state.external_access_url = patch.external_access_url || ''
          state.effective_url = patch.external_access_url === null ? 'https://agents.example:11443' : patch.external_access_url
          state.has_runtime_override = patch.external_access_url !== null
          state.source = patch.external_access_url === null ? 'deployment' : 'runtime'
        }
        if ('product_website_url' in patch) state.product_website_url = patch.product_website_url
      }
      return { ...state }
    })
  })
  afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()))

  it('shows the effective default as the input value without making the form dirty', async () => {
    const wrapper = render()
    await flushPromises()
    expect(wrapper.findAll('.platform-settings__panel')).toHaveLength(1)
    expect(wrapper.findAll('input')).toHaveLength(2)
    const website = wrapper.get('#product-website-url')
    expect((website.element as HTMLInputElement).value).toBe('https://192.168.8.69:11442')
    expect(website.attributes('placeholder')).toBe('https://192.168.8.69:11442')
    expect(wrapper.text()).toContain('Product Website URL')
    expect(wrapper.text()).toContain('Defaults to https://192.168.8.69:11442.')
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeDefined()
    expect(body()).toBeUndefined()
  })

  it('saves only Website when the external access field is unchanged', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.get('#product-website-url').setValue(' https://hyperfilelens.com/ ')
    await button(wrapper, en.platformOps.settings.saveChanges).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ product_website_url: 'https://hyperfilelens.com/' })
    expect(wrapper.findAll('input')[0]!.element.value).toBe('https://agents.example:11443')
    expect(button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults).attributes('disabled')).toBeUndefined()
    expect(mocks.clear).toHaveBeenCalledOnce()
    expect(mocks.profile).toHaveBeenCalledWith(true)
  })

  it('preserves the Website override when saving only external access', async () => {
    state.product_website_url = 'https://hyperfilelens.com/'
    const wrapper = render()
    await flushPromises()
    await wrapper.findAll('input')[0]!.setValue('https://new-agents.example')
    await button(wrapper, en.platformOps.settings.saveChanges).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ external_access_url: 'https://new-agents.example' })
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://hyperfilelens.com/')
  })

  it('saves both changed fields in one request', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.findAll('input')[0]!.setValue('https://new-agents.example')
    await wrapper.get('#product-website-url').setValue('https://hyperfilelens.com/')
    await button(wrapper, en.platformOps.settings.saveChanges).trigger('click')
    await flushPromises()
    expect(body()).toEqual({
      external_access_url: 'https://new-agents.example',
      product_website_url: 'https://hyperfilelens.com/',
    })
    expect(mocks.api.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1)
  })

  it('clears only the Website override when its input is emptied', async () => {
    state.product_website_url = 'https://hyperfilelens.com/'
    const wrapper = render()
    await flushPromises()
    await wrapper.get('#product-website-url').setValue('')
    await button(wrapper, en.platformOps.settings.saveChanges).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ product_website_url: '' })
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://192.168.8.69:11442')
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeDefined()
  })

  it('does not persist the displayed default when only external access changes', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.findAll('input')[0]!.setValue('https://new-agents.example')
    await button(wrapper, en.platformOps.settings.saveChanges).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ external_access_url: 'https://new-agents.example' })
    expect(state.product_website_url).toBe('')
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://192.168.8.69:11442')
  })

  it('allows clearing the displayed default and refills it after saving without pinning an override', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.get('#product-website-url').setValue('  ')
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeUndefined()
    await button(wrapper, en.platformOps.settings.saveChanges).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ product_website_url: '' })
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://192.168.8.69:11442')
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeDefined()
  })

  it('becomes clean again when the Website draft is changed back to the displayed default', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.get('#product-website-url').setValue('https://draft.example/')
    await wrapper.get('#product-website-url').setValue(' https://192.168.8.69:11442 ')
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeDefined()
    expect(body()).toBeUndefined()
  })

  it('uses the backend-provided default port in both the input and description', async () => {
    state.default_product_website_url = 'https://public.example:12442'
    const wrapper = render()
    await flushPromises()
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://public.example:12442')
    expect(wrapper.text()).toContain('Defaults to https://public.example:12442.')
    expect(wrapper.text()).not.toContain('Defaults to https://192.168.8.69:11442.')
  })

  it('restores a Website-only override without changing external access', async () => {
    state.product_website_url = 'https://hyperfilelens.com/'
    const wrapper = render()
    await flushPromises()
    const restore = button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults)
    expect(restore.attributes('disabled')).toBeUndefined()
    await restore.trigger('click')
    await flushPromises()
    expect(body()).toEqual({ product_website_url: '' })
    expect(wrapper.findAll('input')[0]!.element.value).toBe('https://agents.example:11443')
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://192.168.8.69:11442')
    expect(restore.attributes('disabled')).toBeDefined()
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeDefined()
    expect(mocks.clear).toHaveBeenCalledOnce()
    expect(mocks.profile).toHaveBeenCalledWith(true)
  })

  it('keeps Restore Defaults disabled when neither field has a saved override', async () => {
    const wrapper = render()
    await flushPromises()
    const restore = button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults)
    expect(restore.attributes('disabled')).toBeDefined()
    await restore.trigger('click')
    expect(body()).toBeUndefined()
  })

  it('restores both overrides in a single request and displays both effective defaults', async () => {
    state.external_access_url = 'https://runtime-agents.example'
    state.effective_url = state.external_access_url
    state.has_runtime_override = true
    state.source = 'runtime'
    state.product_website_url = 'https://hyperfilelens.com/'
    const wrapper = render()
    await flushPromises()
    await button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ external_access_url: null, product_website_url: '' })
    expect(mocks.api.mock.calls.filter(([, init]) => init?.method === 'PATCH')).toHaveLength(1)
    expect(wrapper.findAll('input')[0]!.element.value).toBe('https://agents.example:11443')
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://192.168.8.69:11442')
    expect(button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults).attributes('disabled')).toBeDefined()
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeDefined()
  })

  it('preserves an unsaved external access draft when restoring only a Website override', async () => {
    state.product_website_url = 'https://hyperfilelens.com/'
    const wrapper = render()
    await flushPromises()
    await wrapper.findAll('input')[0]!.setValue('https://draft-agents.example')
    await button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ product_website_url: '' })
    expect(wrapper.findAll('input')[0]!.element.value).toBe('https://draft-agents.example')
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://192.168.8.69:11442')
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeUndefined()
  })

  it('removes a saved Website override even when it equals the default address', async () => {
    state.product_website_url = state.default_product_website_url
    const wrapper = render()
    await flushPromises()
    const restore = button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults)
    expect(restore.attributes('disabled')).toBeUndefined()
    await restore.trigger('click')
    await flushPromises()
    expect(body()).toEqual({ product_website_url: '' })
    expect(state.product_website_url).toBe('')
    expect(restore.attributes('disabled')).toBeDefined()
  })

  it('keeps both drafts and saved overrides available for retry when restoring fails', async () => {
    state.external_access_url = 'https://runtime-agents.example'
    state.effective_url = state.external_access_url
    state.has_runtime_override = true
    state.source = 'runtime'
    state.product_website_url = 'https://hyperfilelens.com/'
    const wrapper = render()
    await flushPromises()
    await wrapper.findAll('input')[0]!.setValue('https://draft-agents.example')
    await wrapper.get('#product-website-url').setValue('https://draft.example/')
    mocks.api.mockRejectedValueOnce(new Error('Unable to restore settings'))
    await button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ external_access_url: null, product_website_url: '' })
    expect(wrapper.findAll('input')[0]!.element.value).toBe('https://draft-agents.example')
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://draft.example/')
    expect(wrapper.get('[role="alert"]').text()).toBe('Unable to restore settings')
    expect(button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults).attributes('disabled')).toBeUndefined()
    expect(mocks.success).not.toHaveBeenCalled()
  })

  it('restores only external access and preserves an unsaved Website draft', async () => {
    state.external_access_url = 'https://runtime-agents.example'
    state.effective_url = state.external_access_url
    state.has_runtime_override = true
    state.source = 'runtime'
    const wrapper = render()
    await flushPromises()
    await wrapper.get('#product-website-url').setValue('https://draft.example/')
    await button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ external_access_url: null })
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://draft.example/')
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeUndefined()
  })

  it('restores only external access without pinning the displayed Website default', async () => {
    state.external_access_url = 'https://runtime-agents.example'
    state.effective_url = state.external_access_url
    state.has_runtime_override = true
    state.source = 'runtime'
    const wrapper = render()
    await flushPromises()
    const restore = button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults)
    expect(restore.attributes('disabled')).toBeUndefined()
    await restore.trigger('click')
    await flushPromises()
    expect(body()).toEqual({ external_access_url: null })
    expect(state.product_website_url).toBe('')
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('https://192.168.8.69:11442')
    expect(restore.attributes('disabled')).toBeDefined()
    expect(button(wrapper, en.platformOps.settings.saveChanges).attributes('disabled')).toBeDefined()
  })

  it('keeps drafts and shows a single error if saving fails', async () => {
    const wrapper = render()
    await flushPromises()
    await wrapper.get('#product-website-url').setValue('javascript:alert(1)')
    mocks.api.mockRejectedValueOnce(new Error('Invalid product website URL'))
    await button(wrapper, en.platformOps.settings.saveChanges).trigger('click')
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toBe('Invalid product website URL')
    expect((wrapper.get('#product-website-url').element as HTMLInputElement).value).toBe('javascript:alert(1)')
    expect(mocks.success).not.toHaveBeenCalled()
  })

  it('keeps old-backend access settings usable without rendering an unsupported Website field', async () => {
    delete state.product_website_url
    delete state.default_product_website_url
    const wrapper = render()
    await flushPromises()
    expect(wrapper.find('#product-website-url').exists()).toBe(false)
    await wrapper.findAll('input')[0]!.setValue('https://new-agents.example')
    await button(wrapper, en.platformOps.settings.saveChanges).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ external_access_url: 'https://new-agents.example' })
  })

  it('restores external access with an older backend without sending an unsupported Website field', async () => {
    delete state.product_website_url
    delete state.default_product_website_url
    state.external_access_url = 'https://runtime-agents.example'
    state.effective_url = state.external_access_url
    state.has_runtime_override = true
    state.source = 'runtime'
    const wrapper = render()
    await flushPromises()
    await button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults).trigger('click')
    await flushPromises()
    expect(body()).toEqual({ external_access_url: null })
    expect(button(wrapper, en.platformOps.settings.externalAccess.restoreDefaults).attributes('disabled')).toBeDefined()
  })
})
