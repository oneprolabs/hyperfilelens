// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import { en } from '../../../locales/en'
import CopilotMessageList from './CopilotMessageList.vue'

function mountList(props: Record<string, unknown>) {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotMessageList, {
    props: {
      messages: [],
      ...props,
    },
    global: { plugins: [i18n] },
  })
}

describe('CopilotMessageList starter questions and live feedback', () => {
  it('emits a starter question directly and exposes its selected state', async () => {
    const wrapper = mountList({
      messages: [{
        id: 'welcome-1',
        role: 'assistant',
        text: en.insight.copilot.welcome,
        starterChips: true,
      }],
      selectedStarterKey: '',
      starterDisabled: false,
    })
    const firstChip = wrapper.get('.copilot-chip-box')

    expect(firstChip.attributes('aria-pressed')).toBe('false')
    await firstChip.trigger('click')

    expect(wrapper.emitted('starterChip')?.[0]).toEqual([
      'chipQuerySops',
      en.insight.copilot.chipQuerySopsPrompt,
    ])

    await wrapper.setProps({ selectedStarterKey: 'chipQuerySops' })
    expect(firstChip.classes()).toContain('is-selected')
    expect(firstChip.attributes('aria-pressed')).toBe('true')
    wrapper.unmount()
  })

  it('disables starter questions while another submission is active', () => {
    const wrapper = mountList({
      messages: [{
        id: 'welcome-1',
        role: 'assistant',
        text: en.insight.copilot.welcome,
        starterChips: true,
      }],
      starterDisabled: true,
    })

    expect(wrapper.get('.copilot-chip-box').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('shows analysis feedback before SourceLens emits the first progress step', () => {
    const wrapper = mountList({
      messages: [{ id: 'user-1', role: 'user', text: 'What is backed up?' }],
      streaming: true,
      streamingThinking: [],
      streamingElapsedSeconds: 2,
    })

    const livePanel = wrapper.get('.thinking-panel-live')
    const status = livePanel.get('[role="status"]')
    expect(status.attributes('aria-live')).toBe('polite')
    expect(status.text()).toContain('Thinking… 2s')
    expect(livePanel.find('.thinking-panel-body').exists()).toBe(false)
    expect(wrapper.find('.message-card--typing').exists()).toBe(false)
    wrapper.unmount()
  })
})
