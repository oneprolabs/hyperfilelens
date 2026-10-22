// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'
import { en } from '../locales'
import { ChunkLoadError } from './ChunkLoadError'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: { en },
})

describe('ChunkLoadError', () => {
  it('renders an accessible update state and reloads the page', async () => {
    const reload = vi.fn()
    const wrapper = mount(ChunkLoadError, {
      props: {
        error: new TypeError('Failed to fetch dynamically imported module'),
        reload,
      },
      global: { plugins: [i18n] },
    })

    expect(wrapper.attributes('role')).toBe('alert')
    expect(wrapper.attributes('aria-live')).toBe('assertive')
    expect(wrapper.get('h1').text()).toBe('A newer version is ready')
    expect(wrapper.get('.chunk-load-error__message').text()).toContain('previous application version')
    expect(wrapper.get('button').text()).toContain('Reload Now')
    expect(wrapper.find('svg.lucide-refresh-cw').exists()).toBe(true)
    expect(wrapper.get('button').attributes('type')).toBe('button')
    expect(wrapper.find('.chunk-load-error__card').exists()).toBe(true)
    expect(wrapper.find('.chunk-load-error__eyebrow').exists()).toBe(false)
    expect(wrapper.find('.chunk-load-error__icon').exists()).toBe(false)
    expect(wrapper.find('.chunk-load-error__note').exists()).toBe(false)

    await wrapper.get('button').trigger('click')
    expect(reload).toHaveBeenCalledOnce()
  })

  it('uses neutral copy for a non-update loading error', () => {
    const wrapper = mount(ChunkLoadError, {
      props: {
        error: new Error('Route module failed to evaluate'),
        reload: vi.fn(),
      },
      global: { plugins: [i18n] },
    })

    expect(wrapper.get('h1').text()).toBe('This page could not be loaded')
    expect(wrapper.find('.chunk-load-error__message').text()).toContain('contact your administrator')
  })
})
