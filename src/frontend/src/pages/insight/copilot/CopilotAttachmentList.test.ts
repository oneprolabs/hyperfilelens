// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../../locales/en'
import CopilotAttachmentList from './CopilotAttachmentList.vue'

const mocks = vi.hoisted(() => ({
  fetchAttachment: vi.fn(),
}))

vi.mock('../../../lib/lensApi', () => ({
  fetchCopilotAttachmentBlob: mocks.fetchAttachment,
}))

let intersectionCallback: IntersectionObserverCallback

class IntersectionObserverMock {
  constructor(callback: IntersectionObserverCallback) {
    intersectionCallback = callback
  }

  observe() {}
  disconnect() {}
  unobserve() {}
  takeRecords() { return [] }
  readonly root = null
  readonly rootMargin = '240px 0px'
  readonly thresholds = [0]
}

function mountAttachments() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotAttachmentList, {
    props: {
      sessionId: 7,
      attachments: [{
        uuid: '00000000-0000-4000-8000-000000000001',
        kind: 'image',
        mime_type: 'image/png',
        original_name: 'diagram.png',
        url: '/api/v1/lens/copilot/sessions/7/attachments/image/?token=signed',
      }],
    },
    global: {
      plugins: [i18n],
      stubs: { ElImage: true },
    },
  })
}

function abortError() {
  return new DOMException('aborted', 'AbortError')
}

describe('CopilotAttachmentList image loading', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubGlobal('IntersectionObserver', IntersectionObserverMock)
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:preview'),
      revokeObjectURL: vi.fn(),
    })
    mocks.fetchAttachment.mockResolvedValue({
      blob: new Blob(['image'], { type: 'image/png' }),
      filename: 'diagram.png',
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('loads a historical image only when its message approaches the viewport', async () => {
    const wrapper = mountAttachments()

    expect(mocks.fetchAttachment).not.toHaveBeenCalled()
    intersectionCallback(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    )
    await flushPromises()

    expect(mocks.fetchAttachment).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.copilot-message-image').attributes('src')).toBe('blob:preview')
    wrapper.unmount()
  })

  it('revokes partially loaded image URLs when the component is unmounted', async () => {
    mocks.fetchAttachment
      .mockResolvedValueOnce({
        blob: new Blob(['first'], { type: 'image/png' }),
        filename: 'first.png',
      })
      .mockImplementationOnce((...args: unknown[]) => {
        const signal = args[3] as AbortSignal
        return new Promise((_, reject) => {
          signal.addEventListener('abort', () => reject(abortError()), { once: true })
        })
      })
    const wrapper = mountAttachments()
    await wrapper.setProps({
      attachments: [
        ...wrapper.props('attachments'),
        {
          uuid: '00000000-0000-4000-8000-000000000002',
          kind: 'image',
          mime_type: 'image/png',
          original_name: 'second.png',
          url: '/api/v1/lens/copilot/sessions/7/attachments/second/?token=signed',
        },
      ],
    })

    intersectionCallback(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    )
    await flushPromises()
    expect(mocks.fetchAttachment).toHaveBeenCalledTimes(2)

    wrapper.unmount()
    await flushPromises()

    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
  })
})
