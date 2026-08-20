// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../../locales/en'
import CopilotOutputFileList from './CopilotOutputFileList.vue'

const mocks = vi.hoisted(() => ({
  fetchCopilotAttachmentBlob: vi.fn(),
}))

vi.mock('../../../lib/lensApi', async (importOriginal) => ({
  ...await importOriginal<typeof import('../../../lib/lensApi')>(),
  fetchCopilotAttachmentBlob: mocks.fetchCopilotAttachmentBlob,
}))

function mountList() {
  const i18n = createI18n({
    legacy: false,
    locale: 'en',
    messages: { en },
    missingWarn: false,
    fallbackWarn: false,
  })
  return mount(CopilotOutputFileList, {
    props: {
      sessionId: 17,
      files: [{
        uuid: 'a7062479-1d82-4b98-8537-64bf76bb5804',
        url: '/api/v1/lens/copilot/sessions/17/output-files/a7062479-1d82-4b98-8537-64bf76bb5804/content/',
        filename: 'analysis.md',
        content_type: 'text/markdown',
        byte_size: 1536,
      }],
    },
    global: { plugins: [i18n] },
  })
}

describe('CopilotOutputFileList', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
    mocks.fetchCopilotAttachmentBlob.mockReset()
  })

  it('renders SourceLens output metadata and downloads through the HFL proxy', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn(() => 'blob:output-file'),
      revokeObjectURL,
    })
    mocks.fetchCopilotAttachmentBlob.mockResolvedValue({
      blob: new Blob(['# Analysis'], { type: 'text/markdown' }),
      filename: 'analysis.md',
    })
    const wrapper = mountList()

    expect(wrapper.get('.copilot-output-file__name').text()).toBe('analysis.md · 1.5 KB')
    await wrapper.get('.copilot-output-file').trigger('click')
    await flushPromises()

    expect(mocks.fetchCopilotAttachmentBlob).toHaveBeenCalledWith(
      17,
      'a7062479-1d82-4b98-8537-64bf76bb5804',
      '/api/v1/lens/copilot/sessions/17/output-files/a7062479-1d82-4b98-8537-64bf76bb5804/content/',
    )
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:output-file')
    wrapper.unmount()
  })
})
