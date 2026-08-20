// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import { en } from '../../../locales/en'
import CopilotSessionSidebar from './CopilotSessionSidebar.vue'
import type { SessionRow } from './sessionOrdering'

const SlotStub = defineComponent({
  template: '<div><slot /><slot name="dropdown" /></div>',
})

function session(lifecycleStatus: string, overrides: Partial<SessionRow> = {}): SessionRow {
  return {
    id: 7,
    title: 'Quarterly review',
    lifecycle_status: lifecycleStatus,
    status: 'active',
    sl_session_uuid: lifecycleStatus === 'ready' ? 'session-7' : null,
    sl_assistant_uuid: 'assistant-7',
    last_message_at: null,
    last_assistant_message_at: null,
    last_viewed_at: null,
    has_unread: false,
    pinned_at: null,
    created_at: '2026-08-14T08:00:00Z',
    updated_at: '2026-08-14T08:00:00Z',
    group: 'today',
    ...overrides,
  }
}

function mountSidebar(row: SessionRow) {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotSessionSidebar, {
    props: {
      sessions: [row],
      activeId: row.id,
    },
    global: {
      plugins: [i18n],
      directives: { loading: {} },
      stubs: {
        ElButton: SlotStub,
        ElDropdown: SlotStub,
        ElDropdownMenu: SlotStub,
        ElDropdownItem: SlotStub,
      },
    },
  })
}

describe('CopilotSessionSidebar pin actions', () => {
  it('offers pinning only after the SourceLens session is ready', () => {
    const ready = mountSidebar(session('ready'))
    expect(ready.find('.copilot-session-menu__pin').exists()).toBe(true)
    ready.unmount()

    const failed = mountSidebar(session('failed'))
    expect(failed.find('.copilot-session-menu__pin').exists()).toBe(false)
    failed.unmount()
  })

  it('shows recovery state and hides retry until cleanup completes', () => {
    const recovering = mountSidebar(session('failed', {
      cleanup_intent: 'reset_for_retry',
      cleanup_status: 'running',
    }))

    expect(recovering.text()).toContain('Recovering chat…')
    expect(recovering.find('.copilot-session-menu__retry').exists()).toBe(false)
  })

  it('keeps SourceLens-aligned actions in a stable order', () => {
    const wrapper = mountSidebar(session('ready', {
      has_shareable_answer: true,
    }))
    const menuClasses = wrapper.findAll('.copilot-session-menu__share, .copilot-session-menu__rename, .copilot-session-menu__pin, .copilot-session-menu__delete')
      .map((item) => item.classes().find((name) => name.startsWith('copilot-session-menu__')))

    expect(menuClasses).toEqual([
      'copilot-session-menu__share',
      'copilot-session-menu__rename',
      'copilot-session-menu__pin',
      'copilot-session-menu__delete',
    ])
    expect(wrapper.text()).toContain('Share')
    expect(wrapper.text()).toContain('Rename')
    expect(wrapper.text()).toContain('Pin chat')
    expect(wrapper.text()).toContain('Delete session')
  })

  it('disables sharing until a completed answer exists', () => {
    const wrapper = mountSidebar(session('ready', {
      has_shareable_answer: false,
    }))
    const share = wrapper.find('.copilot-session-menu__share')

    expect(share.attributes('disabled')).toBe('true')
    expect(share.attributes('title')).toBe(
      'Share becomes available after the session has a completed answer.',
    )
  })

  it('retains HFL retry for a failed chat', () => {
    const wrapper = mountSidebar(session('failed'))

    expect(wrapper.find('.copilot-session-menu__share').exists()).toBe(false)
    expect(wrapper.find('.copilot-session-menu__retry').exists()).toBe(true)
    expect(wrapper.text()).toContain('Try again')
  })
})
