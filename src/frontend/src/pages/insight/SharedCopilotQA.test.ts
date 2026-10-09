// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { ElButton, ElResult } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { formatLocalDateTime } from '../../lib/dateTime'
import type { LensSharedQA } from '../../lib/lensApi'
import { en } from '../../locales/en'
import SharedCopilotQA from './SharedCopilotQA.vue'

const mocks = vi.hoisted(() => ({
  fetchShare: vi.fn(),
  fetchFile: vi.fn(),
  setScope: vi.fn(),
  push: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: { access: 'signed-access' } }),
  useRouter: () => ({ push: mocks.push }),
}))

vi.mock('../../lib/lensApi', () => ({
  fetchSharedCopilotQA: mocks.fetchShare,
  fetchSharedCopilotFile: mocks.fetchFile,
  setLensApiScope: mocks.setScope,
}))

vi.mock('../../lib/api', () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

const snapshot: LensSharedQA = {
  title: 'Shared Backup Summary',
  assistant_name: 'Backup C · Chat 47',
  question: 'Show test.txt',
  answer: '**Completed**\n\nSeven files.',
  published_at: '2026-10-08T16:09:20+08:00',
  pdf_url: '/api/v1/lens/copilot/shared-qa/pdf/?access=signed-access',
}

function mountPage() {
  return mount(SharedCopilotQA, {
    global: {
      plugins: [createI18n({
        legacy: false,
        locale: 'en',
        messages: { en },
        missingWarn: false,
        fallbackWarn: false,
      })],
      components: { ElButton, ElResult },
      directives: { loading: {} },
    },
  })
}

describe('SharedCopilotQA', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mocks.fetchShare.mockResolvedValue(snapshot)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('keeps the title primary and identifies the snapshot source and publication time', async () => {
    const wrapper = mountPage()
    await flushPromises()
    expect(mocks.fetchShare).toHaveBeenCalledWith('signed-access')
    expect(mocks.setScope).toHaveBeenCalledWith('tenant')
    expect(wrapper.get('h1').text()).toBe(snapshot.title)
    expect(wrapper.findAll('.shared-qa-card__meta dt').map((row) => row.text())).toEqual([
      'Source', 'Shared At',
    ])
    expect(wrapper.get('.shared-qa-card__meta').text()).toContain(snapshot.assistant_name)
    expect(wrapper.get('time').attributes('datetime')).toBe(snapshot.published_at)
    expect(wrapper.get('time').text()).toBe(formatLocalDateTime(snapshot.published_at))
    expect(wrapper.get('.shared-qa-card__question > span').text()).toBe('Question')
    expect(wrapper.get('.shared-qa-card__answer > span').text()).toBe('Answer')
    expect(wrapper.get('.shared-qa-card__answer strong').text()).toBe('Completed')
    expect(wrapper.find('textarea').exists()).toBe(false)
    wrapper.unmount()
  })

  it('returns to the viewer’s Chat list rather than the original Chat', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const button = wrapper.get('.shared-qa-page__back')
    expect(button.text()).toBe('Back to Chats')
    expect(button.classes()).toContain('hfl-btn-with-icon')
    expect(button.classes()).toContain('is-text')
    await button.trigger('click')
    expect(mocks.push).toHaveBeenCalledWith('/insight/copilot')
    wrapper.unmount()
  })

  it('downloads the PDF through the existing signed HFL proxy', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const createObjectURL = vi.fn().mockReturnValue('blob:shared-pdf')
    const revokeObjectURL = vi.fn()
    class TestURL extends URL {
      static createObjectURL = createObjectURL
      static revokeObjectURL = revokeObjectURL
    }
    vi.stubGlobal('URL', TestURL)
    mocks.fetchFile.mockResolvedValue({
      blob: new Blob(['pdf'], { type: 'application/pdf' }),
      filename: 'shared-answer.pdf',
    })
    const wrapper = mountPage()
    await flushPromises()
    const button = wrapper.get('.shared-qa-card__pdf')
    expect(button.text()).toBe('Download PDF')
    expect(button.classes()).toContain('hfl-btn-with-icon')
    expect(button.classes()).not.toContain('el-button--primary')
    await button.trigger('click')
    await flushPromises()
    expect(mocks.fetchFile).toHaveBeenCalledWith(snapshot.pdf_url)
    expect(click).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:shared-pdf')
    wrapper.unmount()
  })

  it('keeps the header usable when publication time or PDF is absent', async () => {
    mocks.fetchShare.mockResolvedValue({ ...snapshot, published_at: undefined, pdf_url: undefined })
    const wrapper = mountPage()
    await flushPromises()
    expect(wrapper.get('h1').text()).toBe(snapshot.title)
    expect(wrapper.find('.shared-qa-card__pdf').exists()).toBe(false)
    expect(wrapper.find('time').exists()).toBe(false)
    expect(wrapper.findAll('.shared-qa-card__meta dt')).toHaveLength(1)
    wrapper.unmount()
  })
})
