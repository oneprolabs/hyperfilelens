// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import { en } from '../../../locales/en'
import CopilotComposer from './CopilotComposer.vue'
import type { CopilotComposerAttachment } from './types'

function mountComposer(props: Record<string, unknown> = {}) {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotComposer, {
    props: {
      modelValue: '',
      attachments: [],
      supportsImages: true,
      supportsDocuments: true,
      ...props,
    },
    global: { plugins: [i18n] },
  })
}

function attachment(status: 'uploading' | 'ready'): CopilotComposerAttachment {
  return {
    key: 'attachment-1',
    uuid: status === 'ready' ? '00000000-0000-4000-8000-000000000001' : '',
    kind: 'document',
    original_name: 'report.pdf',
    status,
  }
}

describe('CopilotComposer attachments', () => {
  it('disables the attachment picker when SourceLens exposes no capability', () => {
    const wrapper = mountComposer({
      supportsImages: false,
      supportsDocuments: false,
    })

    expect(wrapper.get('.copilot-attach-btn').attributes('disabled')).toBeDefined()
  })

  it('allows a completed attachment to be sent without text', async () => {
    const wrapper = mountComposer({ attachments: [attachment('ready')] })
    const send = wrapper.get('.copilot-send-btn')

    expect(send.attributes('disabled')).toBeUndefined()
    await send.trigger('click')

    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('blocks sending while an attachment is uploading', () => {
    const wrapper = mountComposer({ attachments: [attachment('uploading')] })

    expect(wrapper.get('.copilot-send-btn').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain(en.insight.copilot.attachmentUploading)
  })

  it('forwards pasted images through the attachment event', async () => {
    const wrapper = mountComposer()
    const image = new File(['image'], 'diagram.png', { type: 'image/png' })

    await wrapper.get('textarea').trigger('paste', {
      clipboardData: { files: [image] },
    })

    expect(wrapper.emitted('attach')?.[0]).toEqual([[image]])
  })
})
