// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../../../locales/en'
import TaskEventFailureDetails from './TaskEventFailureDetails.vue'

describe('TaskEventFailureDetails', () => {
  it('shows actionable guidance and every structured failed file', async () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          source_path: 'E:\\ProgramData',
          failure_details: {
            category: 'source_file_locked',
            count: 2,
            remediation: ['exclude_runtime_cache', 'use_vss', 'stop_owning_service'],
            items: [
              { path: 'Veeam/PerfCache/cpu/LOCK', error: 'locked' },
              { path: 'Veeam/PerfCache/memory/LOCK', error: 'locked' },
            ],
          },
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.text()).toContain('2 files could not be read because another process locked them.')
    expect(wrapper.text()).toContain('How to resolve')
    expect(wrapper.text()).toContain('Use a Windows VSS or application-aware snapshot')
    expect(wrapper.text()).toContain('View all 2 affected files')
    expect(wrapper.text()).toContain('E:\\ProgramData\\Veeam\\PerfCache\\cpu\\LOCK')
    expect(wrapper.findAll('.task-event-failure__files li')).toHaveLength(2)
  })

  it('renders nothing without structured failure details', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: { metadata: { error_message: 'plain failure' } },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.find('.task-event-failure').exists()).toBe(false)
  })

  it('identifies the snapshot and failed directories for Finalize events', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          backup_summary: {
            snapshot_id: 'bss-35ad59a1755f44089d23',
            failed_directories: [{ path: 'E:\\ProgramData', error_code: 'SOURCE_FILE_LOCKED', error_message: 'locked' }],
          },
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.text()).toContain('Snapshot ID')
    expect(wrapper.text()).toContain('bss-35ad59a1755f44089d23')
    expect(wrapper.text()).toContain('Failed directories')
    expect(wrapper.text()).toContain('E:\\ProgramData')
  })

  it('shows skipped file and directory paths as a warning', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          source_path: 'E:\\ProgramData',
          skipped_details: {
            count: 2,
            file_count: 1,
            directory_count: 1,
            reported_count: 2,
            truncated: false,
            items: [
              { path: 'Veeam/PerfCache/cpu/LOCK', error: 'sharing violation', item_type: 'file' },
              { path: 'System Volume Information', error: 'readdir: access denied', item_type: 'directory' },
            ],
          },
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.find('.task-event-failure--warning').exists()).toBe(true)
    expect(wrapper.text()).toContain('2 unreadable items were skipped (files: 1; directories: 1).')
    expect(wrapper.text()).toContain('View 2 skipped items')
    expect(wrapper.text()).toContain('E:\\ProgramData\\Veeam\\PerfCache\\cpu\\LOCK')
    expect(wrapper.text()).toContain('readdir: access denied')
  })

  it('shows only skipped counts for a Finalize summary event', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          skipped_item_count: 5,
          skipped_file_count: 4,
          skipped_directory_count: 1,
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.text()).toContain('5 unreadable items were skipped (files: 4; directories: 1).')
    expect(wrapper.find('details').exists()).toBe(false)
  })
})
