// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { ElImage } from 'element-plus'
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
const previewWrappers: ReturnType<typeof mount>[] = []

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

function mountAttachments(realImage = false) {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  const wrapper = mount(CopilotAttachmentList, {
    attachTo: realImage ? document.body : undefined,
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
      stubs: { ElImage: !realImage },
    },
  })
  if (realImage) previewWrappers.push(wrapper)
  return wrapper
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
    for (const wrapper of previewWrappers.splice(0)) wrapper.unmount()
    vi.restoreAllMocks()
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

  it('renders a real historical image and opens and closes its teleported preview', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const wrapper = mountAttachments(true)

    expect(mocks.fetchAttachment).not.toHaveBeenCalled()
    intersectionCallback(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    )
    await flushPromises()

    expect(wrapper.findComponent(ElImage).exists()).toBe(true)
    const image = wrapper.get('img')
    expect(image.attributes('src')).toBe('blob:preview')
    expect(image.attributes('alt')).toBe('diagram.png')
    await image.trigger('load')
    await image.trigger('click')
    await flushPromises()

    const viewer = document.querySelector('.el-image-viewer__wrapper')
    expect(viewer).not.toBeNull()
    expect(wrapper.element.contains(viewer)).toBe(false)
    expect(viewer?.querySelector('img')?.getAttribute('src')).toBe('blob:preview')
    const close = viewer?.querySelector<HTMLElement>('.el-image-viewer__close')
    expect(close).not.toBeNull()
    close?.click()
    await flushPromises()

    expect(document.querySelector('.el-image-viewer__wrapper')).toBeNull()
    expect(wrapper.get('img').attributes('src')).toBe('blob:preview')
    expect(mocks.fetchAttachment).toHaveBeenCalledTimes(1)
    expect(warn.mock.calls.some(([message]) =>
      String(message).includes('Failed to resolve component: ElImage'),
    )).toBe(false)
  })

  it('previews a newly added image and cleans up its viewer and blob on unmount', async () => {
    const wrapper = mountAttachments(true)
    await wrapper.setProps({ attachments: [] })
    intersectionCallback(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    )
    await flushPromises()
    expect(mocks.fetchAttachment).not.toHaveBeenCalled()

    await wrapper.setProps({
      attachments: [{
        uuid: '00000000-0000-4000-8000-000000000002',
        kind: 'image',
        mime_type: 'image/png',
        original_name: 'new-screenshot.png',
        url: '/api/v1/lens/copilot/sessions/7/attachments/new/?token=signed',
      }],
    })
    await flushPromises()
    await wrapper.get('img').trigger('load')
    await wrapper.get('img').trigger('click')
    await flushPromises()
    expect(document.querySelector('.el-image-viewer__wrapper')).not.toBeNull()
    expect(mocks.fetchAttachment).toHaveBeenCalledTimes(1)

    wrapper.unmount()
    previewWrappers.splice(previewWrappers.indexOf(wrapper), 1)
    await flushPromises()
    expect(document.querySelector('.el-image-viewer__wrapper')).toBeNull()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:preview')
    expect(mocks.fetchAttachment.mock.calls[0]?.[3].aborted).toBe(true)
  })

  it('disposes the old preview and reloads images when switching Chats', async () => {
    vi.mocked(URL.createObjectURL)
      .mockReturnValueOnce('blob:first-chat')
      .mockReturnValueOnce('blob:second-chat')
    const wrapper = mountAttachments(true)
    intersectionCallback(
      [{ isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver,
    )
    await flushPromises()
    await wrapper.get('img').trigger('click')
    await flushPromises()
    expect(document.querySelector('.el-image-viewer__wrapper')).not.toBeNull()

    await wrapper.setProps({
      sessionId: 8,
      attachments: [{
        uuid: '00000000-0000-4000-8000-000000000002',
        kind: 'image',
        mime_type: 'image/png',
        original_name: 'second-chat.png',
        url: '/api/v1/lens/copilot/sessions/8/attachments/image/?token=signed',
      }],
    })
    await flushPromises()

    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:first-chat')
    expect(mocks.fetchAttachment.mock.calls[0]?.[3].aborted).toBe(true)
    expect(mocks.fetchAttachment.mock.calls[1]?.[0]).toBe(8)
    expect(wrapper.get('img').attributes('src')).toBe('blob:second-chat')
    expect(document.querySelector('.el-image-viewer__wrapper')).toBeNull()
    await wrapper.get('img').trigger('click')
    await flushPromises()
    expect(document.querySelector('.el-image-viewer__wrapper img')?.getAttribute('src'))
      .toBe('blob:second-chat')
  })
})
