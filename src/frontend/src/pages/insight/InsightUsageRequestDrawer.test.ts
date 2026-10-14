// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it, vi } from 'vitest'
import { en } from '../../locales/en'
import type { LensCopilotUsageDetail, LensCopilotUsageItem } from '../../lib/lensApi'
import InsightUsageRequestDrawer from './InsightUsageRequestDrawer.vue'

const mocks = vi.hoisted(() => ({ fetchCopilotUsageDetail: vi.fn() }))
vi.mock('../../lib/lensApi', () => mocks)
vi.mock('../../lib/api', () => ({
  apiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

function row(id: string) {
  return { run_uuid: id } as LensCopilotUsageItem
}
function detail(id: string) {
  return {
    run_uuid: id, question: `Question ${id}`, answer: `Answer ${id}`,
    chat_title: 'Chat', backup_source_name: 'Source', status: 'done',
    source_scopes: [], gateway_mode: 'auto',
  } as LensCopilotUsageDetail
}
function mountDrawer() {
  return mount(InsightUsageRequestDrawer, {
    props: { modelValue: true, row: row('a') },
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      directives: { loading: () => undefined },
      stubs: {
        ElDrawer: {
          props: ['modelValue', 'size'],
          template: '<aside v-if="modelValue"><slot name="header" /><slot /></aside>',
        },
        ElAlert: {
          props: ['title'],
          template: '<div role="alert">{{ title }}</div>',
        },
      },
    },
  })
}

describe('Usage request detail lifecycle', () => {
  it('ignores an earlier request when another row is selected', async () => {
    let resolveA!: (value: LensCopilotUsageDetail) => void
    mocks.fetchCopilotUsageDetail.mockReset()
    mocks.fetchCopilotUsageDetail.mockImplementation((id: string) => id === 'a'
      ? new Promise<LensCopilotUsageDetail>(resolve => { resolveA = resolve })
      : Promise.resolve(detail('b')))
    const wrapper = mountDrawer()
    await wrapper.setProps({ row: row('b') })
    await flushPromises()
    expect(wrapper.text()).toContain('Answer b')
    resolveA(detail('a'))
    await flushPromises()
    expect(wrapper.text()).toContain('Answer b')
    expect(wrapper.text()).not.toContain('Answer a')
    wrapper.unmount()
  })

  it('clears a closed request and shows a load error on a later failure', async () => {
    let resolveA!: (value: LensCopilotUsageDetail) => void
    mocks.fetchCopilotUsageDetail.mockReset()
    mocks.fetchCopilotUsageDetail.mockImplementationOnce(() =>
      new Promise<LensCopilotUsageDetail>(resolve => { resolveA = resolve }))
    const wrapper = mountDrawer()
    await wrapper.setProps({ modelValue: false })
    resolveA(detail('a'))
    await flushPromises()
    mocks.fetchCopilotUsageDetail.mockRejectedValueOnce(new Error('private diagnostic'))
    await wrapper.setProps({ modelValue: true, row: row('b') })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Answer a')
    expect(wrapper.get('[role="alert"]').text()).toBe(en.insight.usage.detail.loadFailed)
    wrapper.unmount()
  })

  it('keeps the ledger overview readable when the answer is unavailable', async () => {
    mocks.fetchCopilotUsageDetail.mockReset()
    mocks.fetchCopilotUsageDetail.mockResolvedValue({
      ...detail('a'), answer: '', content_available: false,
      error: 'The AI model timed out. Try again or choose another model.',
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.text()).toContain('Question a')
    expect(wrapper.text()).toContain(en.insight.usage.detail.answerUnavailable)
    expect(wrapper.findAll('.hfl-detail-section__title').map(item => item.text()))
      .toEqual(['Overview', 'Question', 'Answer', 'Error'])
    wrapper.unmount()
  })
})
