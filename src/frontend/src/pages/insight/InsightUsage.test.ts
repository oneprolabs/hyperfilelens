// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import ElementPlus, { ElDatePicker, ElDrawer, ElSelect } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { en } from '../../locales/en'
import InsightUsage from './InsightUsage.vue'

const mocks = vi.hoisted(() => ({ fetchCopilotUsage: vi.fn(), fetchCopilotUsageDetail: vi.fn() }))
vi.mock('../../lib/lensApi', () => mocks)
vi.mock('../../composables/useInsightSideNav', () => ({
  useInsightSideNav: () => [],
}))

async function mountPage() {
  const wrapper = mount(InsightUsage, {
    global: {
      plugins: [
        ElementPlus,
        createI18n({ legacy: false, locale: 'en', messages: { en } }),
      ],
      stubs: {
        ElDrawer: {
          props: ['modelValue', 'size', 'direction'],
          template: '<aside v-if="modelValue"><slot name="header" /><slot /></aside>',
        },
        ModulePage: { template: '<main><slot /></main>' },
        ElDatePicker: {
          props: ['modelValue'],
          emits: ['update:modelValue', 'change'],
          template: '<div class="test-date-range" />',
        },
        HflPagination: {
          props: ['total', 'currentPage', 'pageSize'],
          emits: ['update:currentPage', 'current-change'],
          template: `<button class="test-next-page" @click="$emit('update:currentPage', 2); $emit('current-change', 2)">Next</button>`,
        },
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('Insight Usage history-only page', () => {
  beforeEach(() => {
    mocks.fetchCopilotUsage.mockReset()
    mocks.fetchCopilotUsageDetail.mockReset()
    mocks.fetchCopilotUsageDetail.mockResolvedValue({
      run_uuid: 'run-1', question: 'Where is the backup policy?',
      answer: 'Policies are documented in the backup guide.',
      chat_title: 'Backup questions', backup_source_name: 'Source A', status: 'done',
      time: '2026-10-14T02:00:00Z', started_at: null, finished_at: null,
      gateway_name: 'Gateway A', gateway_mode: 'manual',
      source_scopes: [{ source_path: '/finance', path_type: 'dir' }],
      models_used: ['test-model'], model_calls: 2, total_tokens: 1500,
      estimated_cost: 1.5, error: '',
    })
    mocks.fetchCopilotUsage.mockResolvedValue({
      results: [{
        run_uuid: 'run-1',
        question: 'Where is the backup policy?',
        time: '2026-10-14T02:00:00Z',
        chat_title: 'Backup questions',
        backup_source_name: 'Source A',
        scope_summary: '1 Folder',
        model_calls: 2,
        total_tokens: 1500,
        estimated_cost: null,
        cost_currency: 'USD',
        status: 'done',
      }],
      total: 41,
      backup_sources: ['Source A'],
    })
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders just history, its filters and pagination without dashboard payloads', async () => {
    const wrapper = await mountPage()
    expect(wrapper.findAll('h2')).toHaveLength(0)
    expect(wrapper.text()).toContain('Where is the backup policy?')
    expect(wrapper.text()).toContain('Backup questions')
    expect(wrapper.get('.usage-table__time').text()).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
    expect(wrapper.findAll('.el-table__header-wrapper th').map(column => column.text()))
      .toEqual(['Question', 'Chat', 'Backup Source', 'Status', 'Time'])
    expect(wrapper.find('.test-next-page').exists()).toBe(true)
    expect(wrapper.find('.usage-icon-button').exists()).toBe(false)
    expect(wrapper.find('.usage-panel').exists()).toBe(false)
    expect(wrapper.get('.usage-history-table').attributes('style')).toContain('height: 100%')
    expect(wrapper.get('.usage-toolbar').element.firstElementChild?.className)
      .toBe('usage-history-actions')
    for (const selector of ['.usage-summary-grid', '.usage-trend-panel', '.usage-breakdown-grid', '.usage-chart']) {
      expect(wrapper.find(selector).exists()).toBe(false)
    }
    expect(mocks.fetchCopilotUsage).toHaveBeenCalledWith(expect.objectContaining({
      page: 1,
      page_size: 20,
      q: '',
      status: '',
    }))
    wrapper.unmount()
  })

  it('opens the shared-width user detail drawer without any Usage section', async () => {
    const wrapper = await mountPage()
    await wrapper.get('button.usage-history-question').trigger('click')
    await flushPromises()
    expect(mocks.fetchCopilotUsageDetail).toHaveBeenCalledWith('run-1', true)
    const drawer = wrapper.getComponent(ElDrawer)
    expect(drawer.props('size')).toBe('860px')
    expect(drawer.classes()).toContain('hfl-detail-drawer')
    expect(drawer.text()).toContain('Policies are documented in the backup guide.')
    expect(drawer.text()).toContain('/finance')
    const headings = drawer.findAll('.hfl-detail-section__title').map(item => item.text())
    expect(headings).toEqual(['Overview', 'Question', 'Answer'])
    expect(drawer.text()).not.toContain('Total Tokens')
    expect(drawer.text()).not.toContain('Estimated Cost')
    wrapper.unmount()
  })

  it('preserves pagination, filters and date range', async () => {
    const wrapper = await mountPage()
    await wrapper.get('.test-next-page').trigger('click')
    await flushPromises()
    expect(mocks.fetchCopilotUsage).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }))

    expect(wrapper.findAllComponents(ElSelect)).toHaveLength(1)
    expect(wrapper.text()).not.toContain('All Backup Sources')
    wrapper.findComponent(ElSelect).vm.$emit('update:modelValue', 'failed')
    await flushPromises()
    expect(mocks.fetchCopilotUsage).toHaveBeenLastCalledWith(expect.objectContaining({ status: 'failed' }))

    wrapper.getComponent(ElDatePicker).vm.$emit('change', ['2026-10-01', '2026-10-14'])
    await flushPromises()
    expect(mocks.fetchCopilotUsage).toHaveBeenLastCalledWith(expect.objectContaining({
      start_date: '2026-10-01', end_date: '2026-10-14', page: 1,
    }))
    wrapper.unmount()
  })

  it('describes the empty list as chat requests rather than whole chats', async () => {
    mocks.fetchCopilotUsage.mockResolvedValue({ results: [], total: 0 })
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('No chat requests in this date range.')
    vi.useFakeTimers()
    await wrapper.get('#usage-question-search').setValue('policy')
    await vi.advanceTimersByTimeAsync(320)
    expect(wrapper.text()).toContain('No chat requests match your filters.')
    wrapper.unmount()
  })

  it('keeps debounced question search working', async () => {
    const wrapper = await mountPage()
    vi.useFakeTimers()
    await wrapper.get('#usage-question-search').setValue('  policy  ')
    await vi.advanceTimersByTimeAsync(320)
    expect(mocks.fetchCopilotUsage).toHaveBeenLastCalledWith(expect.objectContaining({
      q: 'policy', page: 1,
    }))
    wrapper.unmount()
  })
})
