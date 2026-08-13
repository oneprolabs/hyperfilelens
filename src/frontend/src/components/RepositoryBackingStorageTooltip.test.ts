// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import { en } from '../locales'
import RepositoryBackingStorageTooltip from './RepositoryBackingStorageTooltip.vue'

describe('RepositoryBackingStorageTooltip', () => {
  it('shows all backing storage facts in one popover', () => {
    const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
    const wrapper = mount(RepositoryBackingStorageTooltip, {
      props: {
        rows: [
          { label: 'Type', value: 'NAS' },
          { label: 'Location', value: 'nas.example.com:/backup' },
          { label: 'Available', value: '222.9 GB' },
          { label: 'Used', value: '70.1 GB' },
          { label: 'Total', value: '293 GB' },
          { label: 'Last Checked', value: '12 minutes ago' },
        ],
        note: 'Used space includes all data on this storage, not only this repository.',
      },
      slots: { default: '<span>222.9 GB available / 293 GB</span>' },
      global: {
        plugins: [i18n],
        stubs: {
          HflPopover: { template: '<section><slot name="reference" /><slot /></section>' },
        },
      },
    })

    expect(wrapper.text()).toContain('Backing Storage')
    expect(wrapper.text()).toContain('nas.example.com:/backup')
    expect(wrapper.text()).toContain('Available222.9 GB')
    expect(wrapper.text()).toContain('Used70.1 GB')
    expect(wrapper.text()).toContain('Total293 GB')
    expect(wrapper.text()).toContain('Last Checked12 minutes ago')
    expect(wrapper.text()).not.toContain('cached')
  })
})
