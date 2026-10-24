// @vitest-environment jsdom

import { shallowMount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../../locales/en'
import type { GatewayAiStatus } from '../../lib/lensApi'
import GatewayLensBasicSection from './GatewayLensBasicSection.vue'

function mountSection(overrides: Partial<GatewayAiStatus>) {
  return shallowMount(GatewayLensBasicSection, {
    props: {
      gatewayAi: {
        gateway_id: 1, ai_enabled: true, sl_lensnode_uuid: 'existing-id',
        sidecar_status: 'online', workspace_root: '/workspace', ...overrides,
      },
    },
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: {
        ElButton: true,
        ElTag: { props: ['type'], template: '<span class="test-ai-status" :data-type="type"><slot /></span>' },
      },
    },
  })
}

describe('Gateway AI Engine detail lifecycle status', () => {
  it.each([
    ['removing', 'Removing AI Engine'],
    ['upgrading', 'Upgrading AI Engine'],
  ])('prioritizes %s over stale SL runtime and readiness', async (operation, label) => {
    const wrapper = mountSection({
      sidecar_status: operation, sl_status: 'online', ai_enabled: false,
    })
    expect(wrapper.get('.test-ai-status').text()).toBe(label)
    expect(wrapper.get('.test-ai-status').attributes('data-type')).toBe('info')
    await wrapper.setProps({
      gatewayAi: {
        ...wrapper.props('gatewayAi')!, sidecar_status: 'online',
        sl_status: 'online', ai_enabled: true,
      },
    })
    expect(wrapper.get('.test-ai-status').text()).toBe('AI Engine Online')
    wrapper.unmount()
  })

  it.each([
    ['online', 'AI Engine Online'],
    ['offline', 'AI Engine Offline'],
    ['error', 'AI Engine Error'],
    ['pending', 'Installing AI Engine'],
  ])('preserves the normal %s display', (status, label) => {
    const wrapper = mountSection({ sidecar_status: status })
    expect(wrapper.get('.test-ai-status').text()).toBe(label)
    wrapper.unmount()
  })
})
