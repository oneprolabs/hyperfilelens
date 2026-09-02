// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { shallowMount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AuthTurnstileField from './AuthTurnstileField.vue'

const baseProps = {
  pending: false,
  ready: false,
  blocked: false,
  configError: false,
  verified: false,
  siteKey: 'test-site-key',
  action: 'login',
  loadingMessage: 'Loading Cloudflare human verification...',
  blockedMessage: 'Cloudflare verification unavailable.',
  configErrorMessage: 'Something went wrong. Please try again.',
  retryLabel: 'Retry',
  manualRetryLabel: 'Verification taking longer than expected? Reload',
  errorCodeLabel: '',
}

function mountField(overrides: Partial<typeof baseProps> & { errorMessage?: string } = {}) {
  return shallowMount(AuthTurnstileField, {
    props: { ...baseProps, ...overrides },
    global: {
      stubs: {
        KeyRound: true,
        TurnstileWidget: true,
      },
    },
  })
}

describe('AuthTurnstileField display states', () => {
  it('renders the configuration loading state without mounting the widget', () => {
    const wrapper = mountField({ pending: true })

    expect(wrapper.find('.auth-turnstile-field__loading').exists()).toBe(true)
    expect(wrapper.find('.auth-turnstile-field__widget').exists()).toBe(false)
    expect(wrapper.text()).toContain(baseProps.loadingMessage)
  })

  it('keeps verification errors visible below a ready Cloudflare widget', () => {
    const wrapper = mountField({ ready: true, errorMessage: 'Human verification failed or expired' })

    expect(wrapper.find('.auth-turnstile-field__widget').exists()).toBe(true)
    expect(wrapper.find('.auth-turnstile-field__viewport').exists()).toBe(true)
    expect(wrapper.get('.auth-turnstile-field__error').text()).toBe('Human verification failed or expired')
    expect(wrapper.get('.auth-turnstile-field__error').attributes('role')).toBe('alert')
  })

  it('forwards challenge invalidation separately from expiration', async () => {
    const wrapper = mountField({ ready: true })
    const widget = wrapper.getComponent({ name: 'TurnstileWidget' })

    widget.vm.$emit('invalidate')
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('invalidate')).toHaveLength(1)
    expect(wrapper.emitted('expire')).toBeUndefined()
  })

  it('offers manual recovery only while widget loading is unusually slow', async () => {
    const wrapper = mountField({ ready: true })
    const widget = wrapper.getComponent({ name: 'TurnstileWidget' })

    expect(wrapper.find('.auth-turnstile-field__manual-retry').exists()).toBe(false)

    widget.vm.$emit('slow-load')
    await wrapper.vm.$nextTick()

    const retry = wrapper.get('.auth-turnstile-field__manual-retry')
    expect(retry.text()).toBe(baseProps.manualRetryLabel)
    expect(retry.element.tagName).toBe('BUTTON')
    expect(retry.find('.auth-turnstile-field__manual-retry-icon').exists()).toBe(true)

    await retry.trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
    expect(wrapper.find('.auth-turnstile-field__manual-retry').exists()).toBe(false)

    widget.vm.$emit('slow-load')
    await wrapper.vm.$nextTick()
    widget.vm.$emit('rendered')
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.auth-turnstile-field__manual-retry').exists()).toBe(false)
  })

  it('shows an unavailable message only once and exposes retry', async () => {
    const wrapper = mountField({
      blocked: true,
      errorMessage: baseProps.blockedMessage,
      errorCodeLabel: 'Reference code: 300030',
    })

    expect(wrapper.find('.auth-turnstile-field__blocked').exists()).toBe(true)
    expect(wrapper.find('.auth-turnstile-field__error').exists()).toBe(false)
    expect(wrapper.text().match(/Cloudflare verification unavailable\./g)).toHaveLength(1)
    expect(wrapper.get('.auth-turnstile-field__reference-code').text()).toBe(
      'Reference code: 300030',
    )

    await wrapper.get('.auth-turnstile-field__retry').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('shows a configuration error only once and exposes retry', () => {
    const wrapper = mountField({
      configError: true,
      errorMessage: baseProps.configErrorMessage,
    })

    expect(wrapper.find('.auth-turnstile-field__blocked').exists()).toBe(true)
    expect(wrapper.find('.auth-turnstile-field__error').exists()).toBe(false)
    expect(wrapper.text().match(/Something went wrong\. Please try again\./g)).toHaveLength(1)
  })

  it('assigns exactly one frame owner to every visual state', () => {
    const fieldSource = readFileSync(
      resolve(process.cwd(), 'src/components/auth/AuthTurnstileField.vue'),
      'utf8',
    )
    const widgetSource = readFileSync(
      resolve(process.cwd(), 'src/components/TurnstileWidget.vue'),
      'utf8',
    )

    expect(fieldSource).toMatch(
      /\.auth-turnstile-field__loading,\s*\.auth-turnstile-field__blocked\s*{[^}]*background:\s*#313131;[^}]*border:\s*1px solid #3a3b40;[^}]*border-radius:[^}]*overflow:\s*hidden;/s,
    )
    expect(fieldSource).toMatch(
      /\.auth-turnstile-field__widget\s*{[^}]*background:\s*transparent;[^}]*border:\s*0;[^}]*border-radius:\s*0;[^}]*overflow:\s*visible;/s,
    )
    expect(fieldSource).toMatch(
      /\.auth-turnstile-field__viewport\s*{[^}]*border:\s*1px solid #3a3b40;[^}]*border-radius:[^}]*overflow:\s*hidden;/s,
    )
    expect(fieldSource).toMatch(
      /\.auth-turnstile-field__viewport :deep\(\.turnstile-widget\)\s*{[^}]*width:\s*calc\(100% \+ 2px\);[^}]*margin:\s*-1px;/s,
    )
    expect(fieldSource).toMatch(
      /\.auth-turnstile-field__viewport :deep\(\.turnstile-widget__loading\)\s*{[^}]*border:\s*0;[^}]*border-radius:\s*0;/s,
    )
    expect(fieldSource).toMatch(
      /\.auth-turnstile-field__control\s*{[^}]*min-height:\s*65px;/s,
    )
    expect(fieldSource).toMatch(
      /\.auth-turnstile-field__spinner\s*{[^}]*width:\s*16px;[^}]*height:\s*16px;/s,
    )
    expect(fieldSource).toMatch(
      /\.auth-turnstile-field__manual-retry\s*{[^}]*width:\s*100%;[^}]*background:\s*color-mix\(in srgb, var\(--color-primary\) 6%, transparent\);[^}]*border:\s*1px solid color-mix\(in srgb, var\(--color-primary\) 22%, transparent\);/s,
    )
    expect(widgetSource).toMatch(
      /\.turnstile-widget\s*{[^}]*height:\s*65px;[^}]*min-height:\s*65px;[^}]*position:\s*relative;/s,
    )
    expect(widgetSource).toMatch(
      /\.turnstile-widget__container\s*{[^}]*height:\s*100%;[^}]*min-height:\s*65px;/s,
    )
    expect(widgetSource).toMatch(
      /\.turnstile-widget__loading\s*{[^}]*gap:\s*10px;[^}]*background:\s*#313131;[^}]*border:\s*1px solid #3A3B40;[^}]*border-radius:/s,
    )
  })
})
