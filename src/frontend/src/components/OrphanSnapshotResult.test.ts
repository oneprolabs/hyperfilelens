// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales'
import OrphanSnapshotResult from './OrphanSnapshotResult.vue'

function render(outcome: string) {
  return mount(OrphanSnapshotResult, {
    props: { metadata: {
      event_type: 'orphan_snapshot_result', outcome, snapshot_id: 'a'.repeat(32),
      source: { name: 'Historical source', host: 'host', user: 'user', path: '/backup' },
    } },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('orphan snapshot audit presentation', () => {
  it('shows source, directory and exact identity without reporting deletion on discovery', () => {
    const text = render('discovered').text()
    expect(text).toContain('Discovered orphan snapshots')
    expect(text).not.toContain('Deleted orphan snapshots')
    expect(text).toContain('Historical source · host · user')
    expect(text).toContain('/backup')
    expect(text).toContain('a'.repeat(32))
  })
  it('keeps deletion separate from discovery', () => {
    const text = render('deleted').text()
    expect(text).toContain('Deleted orphan snapshots')
    expect(text).not.toContain('Discovered orphan snapshots')
  })
})
