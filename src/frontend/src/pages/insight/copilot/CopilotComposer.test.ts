// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../../locales/en'
import CopilotComposer from './CopilotComposer.vue'
import type { CopilotComposerAttachment } from './types'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

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
  it('uses the SourceLens placeholder and AI disclaimer copy', () => {
    const wrapper = mountComposer()

    expect(wrapper.get('textarea').attributes('placeholder')).toBe('Ask anything')
    expect(wrapper.get('.copilot-disclaimer').text()).toBe(
      'Responses are AI-generated; verify critical details.',
    )
  })

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

  it('keeps the draft editable while an answer is running without sending a second Run', async () => {
    const wrapper = mountComposer({
      modelValue: 'Next question',
      sending: true,
      canStop: true,
    })
    const textarea = wrapper.get('textarea')

    expect(textarea.attributes('disabled')).toBeUndefined()
    await textarea.setValue('Edited next question')
    await textarea.trigger('keydown', { key: 'Enter' })

    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['Edited next question'])
    expect(wrapper.emitted('send')).toBeUndefined()
    await wrapper.get('.copilot-send-btn--stop').trigger('click')
    expect(wrapper.emitted('stop')).toHaveLength(1)
  })

  it('uses Enter to send while leaving Shift+Enter for a newline', async () => {
    const wrapper = mountComposer({ modelValue: 'Question' })
    const textarea = wrapper.get('textarea')

    await textarea.trigger('keydown', { key: 'Enter', shiftKey: true })
    expect(wrapper.emitted('send')).toBeUndefined()

    await textarea.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('reports composer height changes and disconnects its observer on unmount', async () => {
    let observerCallback: ResizeObserverCallback | undefined
    const observe = vi.fn()
    const disconnect = vi.fn()

    class MockResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        observerCallback = callback
      }

      observe = observe
      unobserve = vi.fn()
      disconnect = disconnect
    }

    vi.stubGlobal('ResizeObserver', MockResizeObserver)
    const wrapper = mountComposer()
    await nextTick()

    const composer = wrapper.get('.copilot-composer').element
    vi.spyOn(composer, 'getBoundingClientRect').mockReturnValue({
      width: 860,
      height: 212,
      top: 0,
      right: 860,
      bottom: 212,
      left: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    })
    observerCallback?.([], {} as ResizeObserver)

    expect(observe).toHaveBeenCalledWith(composer)
    expect(wrapper.emitted('resize')?.at(-1)).toEqual([212])

    wrapper.unmount()
    expect(disconnect).toHaveBeenCalledOnce()
  })
})
