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
    const pending = mountCell({ usedBytes: 0, probeStatus: 'pending' })
    expect(pending.text()).toContain(en.repositoriesPage.usageMeasurementPending)
    expect(pending.text()).not.toContain('≈ 0 GB')
    expect(pending.find('.animate-spin').exists()).toBe(false)
  })

  it('keeps the known limit when usage collection fails', () => {
    const text = mountCell({ probeStatus: 'failed' }).text()
    expect(text).toContain(en.repositoriesPage.usageMeasurementFailed)
    expect(text).toContain('Configured limit: 200 GB')
  })

  it('opens failure details through a keyboard-accessible button', async () => {
    const wrapper = mountCell({ probeStatus: 'failed', interactive: true })
    expect(wrapper.get('button').text()).toBe(en.repositoriesPage.usageMeasurementFailed)
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('inspect')).toHaveLength(1)
    expect(wrapper.text()).not.toContain('≈ 20 GB')
    wrapper.unmount()
  })

  it.each([0, 20])('keeps the last successful value %s and labels a failed update', (usedBytes) => {
    const wrapper = mountCell({
      usedBytes, probeStatus: 'failed', interactive: true, lastSuccessAt: '2026-10-27T01:00:00Z',
    })
    expect(wrapper.text()).toContain(`≈ ${usedBytes} GB`)
    expect(wrapper.text()).toContain(en.repositoriesPage.usageLastKnownValue)
    expect(wrapper.get('button').text()).toBe(en.repositoriesPage.usageUpdateFailed)
    expect(wrapper.find('.repo-usage-cell__percent').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows a spinner only during an explicit refresh and retains existing usage', () => {
    const wrapper = mountCell({ refreshing: true, interactive: true })
    expect(wrapper.text()).toContain('≈ 20 GB')
    expect(wrapper.text()).toContain(en.repositoriesPage.usageRefreshPending)
    expect(wrapper.text()).toContain(en.repositoriesPage.usageLastKnownValue)
    expect(wrapper.find('.animate-spin').exists()).toBe(true)
    expect(wrapper.find('button').exists()).toBe(false)
    wrapper.unmount()
  })

  it('does not add a success badge or action for current usable measurements', () => {
    const wrapper = mountCell({ interactive: true })
    expect(wrapper.find('.repository-estimated-usage__state').exists()).toBe(false)
    expect(wrapper.find('button').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps historical usage visibly labeled in details even when ordinary supporting text is hidden', () => {
    const wrapper = mountCell({
      probeStatus: 'failed', lastSuccessAt: '2026-10-27T01:00:00Z', variant: 'detail', showSupporting: false,
    })
    expect(wrapper.text()).toContain('≈ 20 GB')
    expect(wrapper.text()).toContain(en.repositoriesPage.usageLastKnownValue)
    wrapper.unmount()
  })

  it('does not confuse stored pending values with a successful or running measurement', () => {
    const wrapper = mountCell({ probeStatus: 'pending', usedBytes: 20 })
    expect(wrapper.text()).not.toContain('≈ 20 GB')
    expect(wrapper.text()).toContain(en.repositoriesPage.usageMeasurementPending)
    expect(wrapper.find('.animate-spin').exists()).toBe(false)
    wrapper.unmount()
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
