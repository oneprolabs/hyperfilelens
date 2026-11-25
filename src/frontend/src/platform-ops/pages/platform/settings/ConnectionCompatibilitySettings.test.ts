// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../../../locales/en'
import ConnectionCompatibilitySettings from './ConnectionCompatibilitySettings.vue'

const mocks = vi.hoisted(() => ({
  patch: vi.fn(), success: vi.fn(), error: vi.fn(),
}))
vi.mock('../../../lib/platformOpsApi', () => ({
  patchPlatformConnectionCompatibility: mocks.patch,
}))
vi.mock('element-plus', () => ({
  ElMessage: { success: mocks.success, error: mocks.error },
}))
vi.mock('../../../../lib/api', () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

const Switch = defineComponent({
  inheritAttrs: false,
  props: ['modelValue', 'disabled'],
  emits: ['update:modelValue', 'change'],
  setup(props, { attrs, emit }) {
    return () => h('input', {
      ...attrs, type: 'checkbox', checked: props.modelValue, disabled: props.disabled,
      onChange: (event: Event) => {
        const value = (event.target as HTMLInputElement).checked
        emit('update:modelValue', value)
        emit('change', value)
      },
    })
  },
})
const wrappers: ReturnType<typeof mount>[] = []
function render(initialValue = false) {
  const wrapper = mount(ConnectionCompatibilitySettings, {
    props: { initialValue },
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: {
        ElSwitch: Switch,
      },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}
beforeEach(() => {
  vi.clearAllMocks()
  mocks.patch.mockImplementation(async (enabled: boolean) => ({
    compatibility: { aliyun_sts_proxy_compatibility_enabled: enabled },
  }))
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()) })

describe('External Access connection compatibility row', () => {
  it('defaults off and saves immediately when toggled without reloading health', async () => {
    const wrapper = render()
    await flushPromises()
    const input = wrapper.get<HTMLInputElement>('#aliyun-sts-proxy-compatibility')
    expect(input.element.checked).toBe(false)
    expect(wrapper.find('button').exists()).toBe(false)
    expect(wrapper.classes()).toContain('platform-settings__setting-row')
    expect(wrapper.find('.platform-settings__panel').exists()).toBe(false)
    expect(wrapper.find('h3').exists()).toBe(false)
    expect(mocks.patch).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Alibaba Cloud STS Proxy Compatibility')
    expect(wrapper.get('.platform-settings__hint').text()).toBe(
      'Enable if Alibaba Cloud STS requests do not respect NO_PROXY. Applies only to temporary credential requests, not object storage traffic.',
    )
    await input.setValue(true)
    await flushPromises()
    expect(mocks.patch).toHaveBeenCalledExactlyOnceWith(true)
    expect(wrapper.emitted('saved')).toEqual([[true]])
    expect(mocks.success).toHaveBeenCalledWith(expect.objectContaining({ message: 'Settings saved' }))
    expect(wrapper.get('input').attributes('disabled')).toBeUndefined()
  })

  it('loads a saved enabled setting and supports disabling it', async () => {
    const wrapper = render(true)
    await flushPromises()
    expect(wrapper.get<HTMLInputElement>('input').element.checked).toBe(true)
    await wrapper.get('input').setValue(false)
    await flushPromises()
    expect(mocks.patch).toHaveBeenCalledExactlyOnceWith(false)
  })

  it('rolls back the switch on failure and allows retrying', async () => {
    mocks.patch.mockRejectedValueOnce(new Error('failed'))
    const wrapper = render()
    await flushPromises()
    await wrapper.get('input').setValue(true)
    await flushPromises()
    expect(mocks.error).toHaveBeenCalledOnce()
    expect(wrapper.get<HTMLInputElement>('input').element.checked).toBe(false)
    expect(wrapper.get('input').attributes('disabled')).toBeUndefined()
    await wrapper.get('input').setValue(true)
    await flushPromises()
    expect(mocks.patch).toHaveBeenCalledTimes(2)
  })

  it('rolls back disabling on failure instead of displaying an unsaved off state', async () => {
    mocks.patch.mockRejectedValueOnce(new Error('failed'))
    const wrapper = render(true)
    await flushPromises()
    await wrapper.get('input').setValue(false)
    await flushPromises()
    expect(wrapper.get<HTMLInputElement>('input').element.checked).toBe(true)
    expect(mocks.patch).toHaveBeenCalledExactlyOnceWith(false)
  })

  it('rejects an incomplete save response instead of reporting success', async () => {
    mocks.patch.mockResolvedValueOnce({})
    const wrapper = render()
    await flushPromises()
    await wrapper.get('input').setValue(true)
    await flushPromises()
    expect(wrapper.get<HTMLInputElement>('input').element.checked).toBe(false)
    expect(mocks.success).not.toHaveBeenCalled()
    expect(mocks.error).toHaveBeenCalledOnce()
  })

  it('blocks duplicate saves and ignores results after unmounting', async () => {
    let finish!: (result: unknown) => void
    mocks.patch.mockReturnValue(new Promise(resolve => { finish = resolve }))
    const wrapper = render()
    await flushPromises()
    await wrapper.get('input').setValue(true)
    await wrapper.get('input').trigger('change')
    expect(mocks.patch).toHaveBeenCalledTimes(1)
    expect(wrapper.get('input').attributes('disabled')).toBeDefined()
    wrapper.unmount()
    finish({ compatibility: { aliyun_sts_proxy_compatibility_enabled: true } })
    await flushPromises()
    expect(mocks.success).not.toHaveBeenCalled()
  })

  it('does not overwrite its saved state with an old address-save snapshot', async () => {
    const wrapper = render()
    await wrapper.get('input').setValue(true)
    await flushPromises()
    await wrapper.setProps({ initialValue: false })
    expect(wrapper.get<HTMLInputElement>('input').element.checked).toBe(true)
    expect(mocks.patch).toHaveBeenCalledTimes(1)
  })
})
