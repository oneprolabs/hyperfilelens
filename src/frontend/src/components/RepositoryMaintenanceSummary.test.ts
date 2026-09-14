// @vitest-environment jsdom

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales'
import RepositoryMaintenanceSummary from './RepositoryMaintenanceSummary.vue'

const zhHans = JSON.parse(readFileSync(
  resolve(process.cwd(), '../../language-packs/packs/zh-hans/frontend/messages.json'),
  'utf8',
)) as {
  ops: {
    task: {
      maintenanceSummary: Record<string, string>
    }
  }
}

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
    expect(wrapper.findAll('th').map((header) => header.text())).toEqual([
      'Result',
      'Content count',
      'Data size',
      'Result',
      'Pack count',
      'Data size',
    ])
    expect(wrapper.findAll('tbody tr').map((row) => row.text())).toEqual([
      'Removed from index55 items3.00 MB',
      'Deferred by safety policy7,214 items6.90 GB',
      'Still referenced78,672 items1.20 GB',
      'Deleted from storage225 packs55.0 MB',
      'Retained by safety policy1 packs194 B',
    ])
    expect(wrapper.find('[data-label="Content count"]').text()).toBe('55 items')
    expect(wrapper.find('[data-label="Data size"]').text()).toBe('3.00 MB')
    expect(wrapper.text()).toContain('Physical Pack GC')
    expect(wrapper.text()).not.toContain('·')
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

  it('shows standard Quick operations and preserves reported zero metrics', () => {
    const wrapper = mountSummary({
      schema_version: 1,
      mode: 'quick',
      source: 'maintenance_info',
      approximate: false,
      content_gc: null,
      pack_gc: null,
      stages: [
        {
          type: 'content_rewrite',
          status: 'completed',
          statistics_available: true,
          metrics: {
            found_count: 0,
            found_bytes: 0,
            rewritten_count: 0,
            rewritten_bytes: 0,
            retained_count: 0,
            retained_bytes: 0,
          },
        },
        {
          type: 'pack_gc',
          status: 'not_run',
          statistics_available: false,
          metrics: null,
        },
        {
          type: 'index_compaction',
          status: 'completed',
          statistics_available: false,
          metrics: null,
        },
        {
          type: 'log_cleanup',
          status: 'completed',
          statistics_available: true,
          metrics: { deleted_count: 0, deleted_bytes: 0, retained_count: 12, retained_bytes: 4096 },
        },
      ],
    })

    expect(wrapper.text()).toContain('Quick Maintenance operations')
    expect(wrapper.text()).toContain('Short Pack rewriteCompletedAvailable')
    expect(wrapper.text()).toContain('Physical Pack GCNot run—')
    expect(wrapper.text()).toContain('Index compactionCompletedNot reported')
    expect(wrapper.text()).toContain('Rewritten0 items0 B')
    expect(wrapper.text()).toContain('Deleted0 logs0 B')
    expect(wrapper.text()).toContain('Retained12 logs4.00 KB')
    expect(wrapper.text()).toContain('Physical Pack GC did not run in this cycle')
    expect(wrapper.text()).not.toContain('·')
  })

  it('shows the Epoch Quick path without unrelated Pack GC claims', () => {
    const wrapper = mountSummary({
      schema_version: 1,
      mode: 'quick',
      source: 'maintenance_info',
      approximate: false,
      content_gc: null,
      pack_gc: null,
      stages: [
        {
          type: 'epoch_compaction',
          status: 'completed',
          statistics_available: true,
          metrics: { superseded_index_count: 9, superseded_index_bytes: 170_917_888, epoch: 42 },
        },
        {
          type: 'epoch_advance',
          status: 'completed',
          statistics_available: true,
          metrics: { current_epoch: 43, advanced: false },
        },
      ],
    })

    expect(wrapper.text()).toContain('Epoch compactionCompletedAvailable')
    expect(wrapper.text()).toContain('Epoch marker advanceCompletedAvailable')
    expect(wrapper.text()).toContain('Superseded indexes9 blobs163 MB')
    expect(wrapper.text()).toContain('No Epoch advance was needed; the current Epoch is 43')
    expect(wrapper.text()).not.toContain('Physical Pack deletion did not run')
  })

  it('provides explicit Simplified Chinese column and count labels', () => {
    const messages = zhHans.ops.task.maintenanceSummary

    expect(messages.result).not.toBe(en.ops.task.maintenanceSummary.result)
    expect(messages.contentCount).not.toBe(en.ops.task.maintenanceSummary.contentCount)
    expect(messages.packCount).not.toBe(en.ops.task.maintenanceSummary.packCount)
    expect(messages.dataSize).not.toBe(en.ops.task.maintenanceSummary.dataSize)
    expect(messages.contentCountValue).toContain('{count}')
    expect(messages.packCountValue).toContain('{count}')
    expect(messages).not.toHaveProperty('metric')
  })
})
