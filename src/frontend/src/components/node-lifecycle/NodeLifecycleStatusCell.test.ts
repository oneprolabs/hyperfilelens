// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import NodeLifecycleStatusCell from './NodeLifecycleStatusCell.vue'
import { en } from '../../locales/en'
import type { ApiNode } from '../../types/node'

function setup(clickable = false, loading = false) {
  return mount(NodeLifecycleStatusCell, {
    props: {
      node: { id: 55, status: 'upgrade_failed' } as ApiNode,
      resolveDisplayStatus: () => ({ labelKey: 'nodeLifecycle.state.upgrade_failed', tagType: 'danger' as const }),
      clickable, loading,
    },
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: { ElTag: { template: '<span><slot /></span>' } },
    },
  })
}

describe('lifecycle diagnostic activation', () => {
  it('uses a keyboard-accessible button only when the consumer provides diagnostics', async () => {
    const wrapper = setup(true)
    expect(wrapper.get('button').attributes('type')).toBe('button')
    expect(wrapper.get('button').attributes('aria-label')).toContain('View details')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
    wrapper.unmount()
  })

  it('does not activate read-only status labels', async () => {
    const wrapper = setup()
    expect(wrapper.find('button').exists()).toBe(false)
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    wrapper.unmount()
  })

  it('shows busy state and prevents repeated activation while loading', async () => {
    const wrapper = setup(true, true)
    expect(wrapper.get('button').attributes('aria-busy')).toBe('true')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    wrapper.unmount()
  })
})
