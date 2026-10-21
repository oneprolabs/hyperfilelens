// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../../../locales/en'
import CopilotEmptyState from './CopilotEmptyState.vue'

describe('Chat empty-state creation access', () => {
  it.each([true, false])('shows Start New Chat only when creation is allowed (%s)', async (canCreateChat) => {
    const wrapper = mount(CopilotEmptyState, {
      props: { phase: 'onboarding', canCreateChat },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
        directives: { loading: {} },
        stubs: { ElButton: { emits: ['click'], template: '<button @click="$emit(\'click\')"><slot /></button>' } },
      },
    })
    expect(wrapper.find('.onboarding-primary').exists()).toBe(canCreateChat)
    if (canCreateChat) {
      await wrapper.find('.onboarding-primary').trigger('click')
      expect(wrapper.emitted('newChat')).toHaveLength(1)
    } else {
      expect(wrapper.text()).toContain(en.insight.copilot.emptyNoSessions)
      expect(wrapper.text()).not.toContain(en.insight.copilot.onboardingSubtitle)
      expect(wrapper.emitted('newChat')).toBeUndefined()
    }
    wrapper.unmount()
  })
})
