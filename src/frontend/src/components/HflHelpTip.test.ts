// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import HflHelpTip from './HflHelpTip.vue'

const global = {
  stubs: {
    ElTooltip: {
      template: '<slot />',
      props: ['content', 'disabled', 'placement', 'showAfter', 'popperClass', 'trigger'],
    },
  },
}

describe('HflHelpTip trigger label', () => {
  it('preserves the existing icon-only trigger by default', () => {
    const wrapper = mount(HflHelpTip, { props: { content: 'Help' }, global })
    expect(wrapper.get('button').text()).toBe('')
    expect(wrapper.get('button').classes()).not.toContain('hfl-help-tip--labeled')
    expect(wrapper.findAll('svg')).toHaveLength(1)
    wrapper.unmount()
  })

  it('renders the label and icon inside one focusable trigger', () => {
    const wrapper = mount(HflHelpTip, {
      props: { label: 'Not selectable', content: 'Protected path', ariaLabel: 'Protected path' },
      global,
    })
    expect(wrapper.findAll('button')).toHaveLength(1)
    const button = wrapper.get('button')
    expect(button.text()).toBe('Not selectable')
    expect(button.findAll('svg')).toHaveLength(1)
    expect(button.attributes('aria-label')).toBe('Not selectable: Protected path')
    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.attributes('aria-disabled')).toBe('false')
    expect(button.classes()).toContain('hfl-help-tip--labeled')
    wrapper.unmount()
  })

  it.each(['mouseenter', 'focus'])('opens a single real tooltip on %s', async (event) => {
    const wrapper = mount(HflHelpTip, {
      attachTo: document.body,
      props: { label: 'Not selectable', content: 'Protected path', ariaLabel: 'Protected path' },
    })
    try {
      expect(wrapper.findAll('[title]')).toHaveLength(0)
      await wrapper.get('button').trigger(event)
      await vi.waitFor(() => {
        const tips = document.querySelectorAll('[role="tooltip"]')
        expect(tips).toHaveLength(1)
        expect(tips[0]?.textContent).toBe('Protected path')
      }, { timeout: 2000 })
    } finally {
      wrapper.unmount()
    }
  })
})
