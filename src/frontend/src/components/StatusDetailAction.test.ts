// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { en } from '../locales/en'
import zh from '../../../../language-packs/packs/zh-hans/frontend/messages.json'
import StatusDetailAction from './StatusDetailAction.vue'

function setup(props: Record<string, unknown> = {}, locale = 'en') {
  return mount(StatusDetailAction, {
    props: { label: 'Failed', ...props },
    slots: { default: '<span data-test="tag">Failed</span>' },
    global: { plugins: [createI18n({ legacy: false, locale, messages: { en, zh } })] },
  })
}

describe('two-line status details', () => {
  it('keeps the tag passive and provides exactly one action below it', async () => {
    const wrapper = setup({ clickable: true, failure: true })
    expect(wrapper.findAll('button')).toHaveLength(1)
    expect(wrapper.get('button').text()).toBe('View details')
    expect(wrapper.get('button').attributes('type')).toBe('button')
    expect(wrapper.get('button').attributes('aria-label')).toBe('Failed: View details')
    expect(wrapper.get('button .status-detail-cell__arrow').attributes('aria-hidden')).toBe('true')
    await wrapper.get('[data-test="tag"]').trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
    wrapper.unmount()
  })

  it('renders no action when the status has no detail handler', async () => {
    const wrapper = setup({ failure: true })
    expect(wrapper.find('button').exists()).toBe(false)
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    wrapper.unmount()
  })

  it('uses task wording for active or blocked operations', () => {
    const wrapper = setup({ clickable: true, failure: false })
    expect(wrapper.get('button').text()).toBe('View task')
    wrapper.unmount()
  })

  it('replaces the action with loading text without changing the tag', async () => {
    const wrapper = setup({ clickable: true, failure: true, loading: true })
    expect(wrapper.get('button').text()).toBe(en.common.loading)
    expect(wrapper.get('button').attributes('aria-busy')).toBe('true')
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(wrapper.find('.status-detail-cell__arrow').exists()).toBe(false)
    expect(wrapper.get('[data-test="tag"]').text()).toBe('Failed')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toBeUndefined()
    await wrapper.setProps({ loading: false })
    expect(wrapper.find('.status-detail-cell__arrow').exists()).toBe(true)
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
    wrapper.unmount()
  })

  it('treats the arrow and text as one action', async () => {
    const wrapper = setup({ clickable: true, failure: true })
    await wrapper.get('.status-detail-cell__arrow').trigger('click')
    expect(wrapper.emitted('click')).toHaveLength(1)
    expect(wrapper.findAll('button')).toHaveLength(1)
    wrapper.unmount()
  })

  it('localizes both the visible action and its accessible name', () => {
    const status = zh.nodeLifecycle.state.deregistration_failed
    const action = zh.protection.backupsPage.statusViewDetails
    const wrapper = setup({ label: status, clickable: true, failure: true }, 'zh')
    expect(wrapper.get('button').text()).toBe(action)
    expect(wrapper.get('button').attributes('aria-label')).toBe(
      zh.protection.backupsPage.statusDetailActionLabel.replace('{status}', status).replace('{action}', action),
    )
    wrapper.unmount()
  })

  it.each([
    ['DataProtection.vue', 3],
    ['BackupSources.vue', 2],
  ])('left-aligns connectivity and lifecycle headers and cells consistently in %s', (name, count) => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection', name), 'utf8')
    for (const key of ['colConnectivity', 'colLifecycleStatus']) {
      const pattern = new RegExp(`<el-table-column\\s+:label="t\\('protection\\.sourceResources\\.${key}'\\)"[^>]*>`, 'g')
      const columns = page.match(pattern) || []
      expect(columns).toHaveLength(count)
      columns.forEach(column => {
        expect(column).toContain('align="left"')
        expect(column).toContain('header-align="left"')
        expect(column).not.toContain('align="center"')
      })
    }
  })
})
