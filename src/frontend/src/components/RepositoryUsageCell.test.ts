// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { createI18n } from 'vue-i18n'

import { en } from '../locales'
import RepositoryUsageCell from './RepositoryUsageCell.vue'

function mountCell(props: Record<string, unknown>) {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
  return mount(RepositoryUsageCell, {
    props: {
      usedBytes: 20,
      limitBytes: 200,
      probeStatus: 'success',
      formatBytes: (value: number) => `${value} GB`,
      ...props,
    },
    global: {
      plugins: [i18n],
      stubs: {
        HflPopover: { template: '<span><slot name="reference" /><slot /></span>' },
      },
    },
  })
}

describe('RepositoryUsageCell', () => {
  it('marks repository data as estimated while preserving the configured limit', () => {
    const wrapper = mountCell({})
    expect(wrapper.text()).toContain('≈ 20 GB')
    expect(wrapper.text()).toContain('/ 200 GB')
    expect(wrapper.text()).toContain('10%')
    expect(wrapper.find('.repo-usage-cell__percent').classes()).toContain('hfl-table-no-tooltip')
    expect(wrapper.text()).toContain('Estimated · Configured limit')
    expect(wrapper.find('.repo-usage-bar').exists()).toBe(false)
  })

  it('distinguishes a valid zero value from pending collection', () => {
    expect(mountCell({ usedBytes: 0 }).text()).toContain('≈ 0 GB')
    expect(mountCell({ usedBytes: 0, probeStatus: 'pending' }).text()).toContain('Waiting for first sync')
  })

  it('keeps the known limit when usage collection fails', () => {
    const text = mountCell({ probeStatus: 'failed' }).text()
    expect(text).toContain('Usage unavailable')
    expect(text).toContain('Configured limit: 200 GB')
  })

  it('shows the same concise capacity warning content used by the detail alert', () => {
    const wrapper = mountCell({ warning: true, storageAvailableBytes: 100 })
    const text = wrapper.text()

    expect(text).toContain('Available storage may be insufficient')
    expect(text).toContain('Remaining Limit≈ 180 GB')
    expect(text).toContain('Available100 GB')
    expect(text).toContain('Remaining Limit = Configured Limit − Estimated Repository Data.')
    expect(text).toContain('Storage may run out before the configured limit is reached.')
    expect(wrapper.find('.repository-capacity-popover__head').exists()).toBe(true)
    expect(wrapper.find('.repository-capacity-popover__icon').exists()).toBe(true)
    expect(wrapper.find('.repository-capacity-popover__metrics').exists()).toBe(true)
    expect(wrapper.find('.repository-capacity-popover__risk').exists()).toBe(true)
    expect(wrapper.find('.repository-capacity-popover__risk svg').exists()).toBe(false)
    expect(wrapper.find('.repository-capacity-popover__formula').text()).toBe('Remaining Limit = Configured Limit − Estimated Repository Data.')
  })
})
