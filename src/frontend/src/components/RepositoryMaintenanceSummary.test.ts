// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales'
import RepositoryMaintenanceSummary from './RepositoryMaintenanceSummary.vue'

function mountSummary(maintenanceSummary: Record<string, unknown>) {
  const i18n = createI18n({ legacy: false, locale: 'en', messages: { en } })
  return mount(RepositoryMaintenanceSummary, {
    props: {
      metadata: {
        event_type: 'repository_maintenance_summary',
        maintenance_summary: maintenanceSummary,
      },
    },
    global: { plugins: [i18n] },
  })
}

describe('RepositoryMaintenanceSummary', () => {
  it('separates logical Content GC from physical Pack deletion', () => {
    const wrapper = mountSummary({
      schema_version: 1,
      mode: 'full',
      source: 'maintenance_info',
      approximate: false,
      content_gc: {
        deleted_count: 55,
        deleted_bytes: 3 * 1024 * 1024,
        deferred_count: 7_214,
        deferred_bytes: 6.9 * 1024 * 1024 * 1024,
        in_use_count: 78_672,
        in_use_bytes: 1.2 * 1024 * 1024 * 1024,
      },
      pack_gc: {
        deleted_count: 225,
        deleted_bytes: 57_644_749,
        retained_count: 1,
        retained_bytes: 194,
      },
    })

    expect(wrapper.text()).toContain('Logical Content GC')
    expect(wrapper.text()).toContain('Removed from index55 · 3.00 MB')
    expect(wrapper.text()).toContain('Deferred by safety policy7,214')
    expect(wrapper.text()).toContain('Physical Pack GC')
    expect(wrapper.text()).toContain('Deleted from storage225')
    expect(wrapper.text()).not.toContain('Approximate values')
  })

  it('does not report zero physical reclamation when the Pack stage is absent', () => {
    const wrapper = mountSummary({
      schema_version: 1,
      mode: 'full',
      source: 'stderr',
      approximate: true,
      content_gc: { deferred_count: 7_214, deferred_bytes: 6.9 * 1024 ** 3 },
      pack_gc: null,
    })

    expect(wrapper.text()).toContain('Physical Pack deletion did not run or was not reported')
    expect(wrapper.text()).toContain('Approximate values parsed from Kopia text output')
    expect(wrapper.text()).not.toContain('Deleted from storage')
  })
})
