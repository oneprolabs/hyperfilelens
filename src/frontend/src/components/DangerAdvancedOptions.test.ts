// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import DangerAdvancedOptions from './DangerAdvancedOptions.vue'

function mountOptions(enabled = false) {
  return mount(DangerAdvancedOptions, {
    props: { expanded: false, enabled, enabledLabel: 'Force Cleanup enabled' },
    slots: { default: '<input type="checkbox"><p>Cleanup explanation</p>' },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('DangerAdvancedOptions', () => {
  it('starts collapsed and toggles visibility without changing the mode', async () => {
    const wrapper = mountOptions()
    const toggle = wrapper.get('button')
    expect(toggle.attributes('type')).toBe('button')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('.danger-advanced-options__content').attributes('style')).toContain('display: none')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    expect(wrapper.get('.danger-advanced-options__content').attributes('style') || '').not.toContain('display: none')
    await toggle.trigger('click')
    expect(wrapper.get('.danger-advanced-options__content').attributes('style')).toContain('display: none')
    expect(wrapper.emitted('update:enabled')).toBeUndefined()
    expect(wrapper.emitted('confirm')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps an enabled mode visible in the header when collapsed and supports external reset', async () => {
    const wrapper = mountOptions(true)
    expect(wrapper.get('[role="status"]').isVisible()).toBe(true)
    await wrapper.setProps({ expanded: true })
    expect(wrapper.get('.danger-advanced-options__content').attributes('style') || '').not.toContain('display: none')
    await wrapper.setProps({ expanded: false, enabled: false })
    expect(wrapper.get('.danger-advanced-options__content').attributes('style')).toContain('display: none')
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('returns focus to the disclosure when externally collapsed', async () => {
    const wrapper = mount(DangerAdvancedOptions, {
      attachTo: document.body,
      props: { expanded: true, enabled: true, enabledLabel: 'Force Cleanup enabled' },
      slots: { default: '<input type="checkbox">' },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    wrapper.get('input').element.focus()
    await wrapper.setProps({ expanded: false })
    expect(document.activeElement).toBe(wrapper.get('button').element)
    wrapper.unmount()
  })
})
