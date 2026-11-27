// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'
import ImpersonationAttribution from './ImpersonationAttribution.vue'

function render(metadata?: unknown) {
  return mount(ImpersonationAttribution, {
    props: { metadata },
    global: { plugins: [createI18n({
      legacy: false,
      locale: 'en',
      messages: { en: { impersonation: { performedBy: 'Performed By', actedAs: 'Acted As' } } },
    })] },
  })
}

describe('task operation impersonation attribution', () => {
  it('shows the operator and effective user for this event', () => {
    const wrapper = render({ impersonation: {
      actor_email: 'support@example.test',
      subject_email: 'customer@example.test',
      session_id: 'internal-session-reference',
    } })
    expect(wrapper.text()).toContain('Performed By: support@example.test')
    expect(wrapper.text()).toContain('Acted As: customer@example.test')
    expect(wrapper.text()).not.toContain('internal-session-reference')
    wrapper.unmount()
  })

  it('falls back to stable identifiers when an email is unavailable', () => {
    const wrapper = render({ impersonation: { actor_email: '', actor_id: 10, subject_id: 20 } })
    expect(wrapper.text()).toContain('Performed By: 10')
    expect(wrapper.text()).toContain('Acted As: 20')
    wrapper.unmount()
  })

  it.each([undefined, null, {}, { reason: 'Normal operation' }, { impersonation: {} }, { impersonation: [] }])(
    'does not add an empty row to ordinary or malformed events: %j', metadata => {
      const wrapper = render(metadata)
      expect(wrapper.find('.impersonation-attribution').exists()).toBe(false)
      wrapper.unmount()
    },
  )

  it('renders account labels as text rather than markup', () => {
    const wrapper = render({ impersonation: { actor_email: '<b>support</b>', subject_email: 'customer' } })
    expect(wrapper.text()).toContain('<b>support</b>')
    expect(wrapper.find('b').exists()).toBe(false)
    wrapper.unmount()
  })
})
