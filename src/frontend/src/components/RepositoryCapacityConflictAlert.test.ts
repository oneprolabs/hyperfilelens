// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'

import { en } from '../locales'
import RepositoryCapacityConflictAlert from './RepositoryCapacityConflictAlert.vue'

function mountAlert(props: Record<string, unknown>) {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
  return mount(RepositoryCapacityConflictAlert, {
    props: {
      configuredLimitBytes: 300,
      estimatedUsageBytes: 20,
      storageAvailableBytes: 100,
      formatBytes: (value: number) => `${value} GB`,
      ...props,
    },
    global: {
      plugins: [i18n],
      stubs: {
        ElAlert: { template: '<section><h3>{{ title }}</h3><slot /></section>', props: ['title'] },
      },
    },
  })
}

describe('RepositoryCapacityConflictAlert', () => {
  it('shows the estimated remaining limit, available storage, formula, and concise risk', () => {
    const text = mountAlert({}).text()

    expect(text).toContain('Available storage may be insufficient')
    expect(text).toContain('Remaining Limit≈ 280 GB')
    expect(text).toContain('Available100 GB')
    expect(text).toContain('Remaining Limit = Configured Limit − Estimated Repository Data.')
    expect(text).toContain('Storage may run out before the configured limit is reached.')
  })

  it('does not render when the configured limit equals available storage', () => {
    expect(mountAlert({ configuredLimitBytes: 120 }).text()).toBe('')
  })

  it('treats a valid zero available value as a capacity conflict', () => {
    expect(mountAlert({ storageAvailableBytes: 0 }).text()).toContain('Available0 GB')
  })
})
