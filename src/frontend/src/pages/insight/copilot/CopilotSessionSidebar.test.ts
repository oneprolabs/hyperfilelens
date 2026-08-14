// @vitest-environment jsdom

import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import { en } from '../../../locales/en'
import CopilotSessionSidebar, { type SessionRow } from './CopilotSessionSidebar.vue'

const SlotStub = defineComponent({
  template: '<div><slot /><slot name="dropdown" /></div>',
})

function session(lifecycleStatus: string): SessionRow {
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
})
