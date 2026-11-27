// @vitest-environment jsdom
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'
import ImpersonationEnded from './ImpersonationEnded.vue'
import { impersonationContext, markImpersonationEnded, returnToAdmin } from '../../lib/impersonation'
import { en } from '../../locales/en'

vi.mock('../../lib/impersonation', async () => {
  const { shallowRef } = await import('vue')
  return {
    impersonationContext: shallowRef(null),
    markImpersonationEnded: vi.fn(),
    returnToAdmin: vi.fn(),
  }
})

let wrapper: VueWrapper | undefined

function render(adminUrl?: string | null) {
  impersonationContext.value = adminUrl === null ? null : { id: 'ended-session', admin_url: adminUrl }
  wrapper = mount(ImpersonationEnded, {
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: {
        ElButton: { template: '<button><slot /></button>' },
      },
    },
  })
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  impersonationContext.value = null
})

describe('impersonation ended page', () => {
  it('shows a neutral end state with a decorative exit icon and an accessible heading', () => {
    const view = render('https://admin.example.test/platform-ops/users')
    expect(markImpersonationEnded).toHaveBeenCalledOnce()
    expect(view.get('h1').text()).toBe('Impersonation Session Ended')
    expect(view.attributes('aria-labelledby')).toBe(view.get('h1').attributes('id'))
    expect(view.attributes('aria-describedby')).toBe(view.get('p').attributes('id'))
    expect(view.get('p').text()).toBe('You can close this tab or return to the Admin Console.')
    const icon = view.get('svg')
    expect(icon.attributes('width')).toBe('32')
    expect(icon.attributes('stroke-width')).toBe('1.75')
    expect(icon.attributes('aria-hidden')).toBe('true')
    expect(icon.attributes('focusable')).toBe('false')
    expect(view.find('[title], [role="tooltip"]').exists()).toBe(false)
  })

  it.each(['https://admin.example.test/platform-ops/users', 'http://localhost:8080/platform-ops/users'])(
    'returns to the existing administrator URL only after an explicit click: %s',
    async adminUrl => {
      const view = render(adminUrl)
      expect(view.findAll('button')).toHaveLength(1)
      expect(view.get('button').text()).toBe('Back to Admin Console')
      expect(returnToAdmin).not.toHaveBeenCalled()
      expect(impersonationContext.value?.id).toBe('ended-session')
      await view.get('button').trigger('click')
      expect(returnToAdmin).toHaveBeenCalledExactlyOnceWith(adminUrl)
    },
  )

  it.each([undefined, '', 'not a URL', '/platform-ops/users', 'javascript:alert(1)', 'data:text/html,admin', 'https://user:password@admin.example.test'])(
    'offers a close-tab instruction without a dead return button for %s',
    adminUrl => {
      const view = render(adminUrl)
      expect(view.get('p').text()).toBe('You can close this tab.')
      expect(view.find('button').exists()).toBe(false)
      expect(markImpersonationEnded).toHaveBeenCalledOnce()
      expect(returnToAdmin).not.toHaveBeenCalled()
    },
  )

  it('also renders safely when the tab has no saved session context', () => {
    const view = render(null)
    expect(view.get('h1').text()).toBe('Impersonation Session Ended')
    expect(view.get('p').text()).toBe('You can close this tab.')
    expect(view.find('button').exists()).toBe(false)
    expect(returnToAdmin).not.toHaveBeenCalled()
  })
})
