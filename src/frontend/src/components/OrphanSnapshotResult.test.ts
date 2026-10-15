// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales'
import OrphanSnapshotResult from './OrphanSnapshotResult.vue'
import { formatLocalDateTime } from '../lib/dateTime'

const createdAt = '2026-09-21T03:33:44.000Z'

function render(outcome: string, snapshotCreatedAt: unknown = createdAt, reason = '') {
  return mount(OrphanSnapshotResult, {
    props: { metadata: {
      event_type: 'orphan_snapshot_result', outcome, snapshot_id: 'a'.repeat(32),
      source: { name: 'Historical source', host: 'host', user: 'user', path: '/backup' },
      snapshot_created_at: snapshotCreatedAt,
      first_seen_at: '2026-10-12T01:00:14.000Z',
      reason,
    } },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
}

describe('orphan snapshot audit presentation', () => {
  it('shows source, directory and exact identity without reporting deletion on discovery', () => {
    const wrapper = render('discovered')
    const text = wrapper.text()
    expect(wrapper.find('strong').exists()).toBe(false)
    expect(text).not.toContain('Discovered orphan snapshots')
    expect(text).not.toContain('Deleted orphan snapshots')
    expect(text).toContain('Historical source · host · user')
    expect(text).toContain('/backup')
    expect(text).toContain('a'.repeat(32))
    expect(text).toContain('Snapshot creation time')
    expect(text).toContain(formatLocalDateTime(createdAt))
  })
  it('does not duplicate the result heading supplied by the event title', () => {
    const text = render('deleted').text()
    expect(text).not.toContain('Deleted orphan snapshots')
    expect(text).not.toContain('Discovered orphan snapshots')
  })
  it('explains the effective disabled setting and retained snapshot for existing audit reasons', () => {
    const text = render('deferred', createdAt, 'Automatic orphan deletion is disabled.').text()
    expect(text).toContain('STORAGE_ORPHAN_SNAPSHOT_DELETE_ENABLED=false')
    expect(text).toContain('check-only mode')
    expect(text).toContain('no deletion was attempted')
    expect(text).toContain('not a snapshot check failure')
    expect(text).not.toContain('Automatic orphan deletion is disabled.')
  })
  it.each([null, 'invalid'])('does not substitute discovery time for unknown creation time (%s)', value => {
    const wrapper = render('discovered', value)
    expect(wrapper.findAll('dd').at(-1)?.text()).toBe('—')
    expect(wrapper.text()).not.toContain('2026-10-12')
  })
  it('does not present an unavailable inventory as an empty repository', () => {
    const wrapper = mount(OrphanSnapshotResult, {
      props: { metadata: {
        event_type: 'orphan_snapshot_summary',
        summary: { inventory_available: false, scanned: null },
      } },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    expect(wrapper.text()).toContain('does not mean the repository is empty')
    expect(wrapper.findAll('dd')).toHaveLength(0)
  })
  it('still displays a successfully scanned empty repository as zero', () => {
    const wrapper = mount(OrphanSnapshotResult, {
      props: { metadata: {
        event_type: 'orphan_snapshot_summary',
        summary: { inventory_available: true, scanned: 0 },
      } },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    expect(wrapper.text()).toContain('Physical snapshots scanned')
    expect(wrapper.find('dd').text()).toBe('0')
    expect(wrapper.text()).not.toContain('inventory unavailable')
  })
})
