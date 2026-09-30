// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../../../locales/en'
import TaskEventFailureDetails from './TaskEventFailureDetails.vue'

describe('TaskEventFailureDetails', () => {
  it.each(['AGENT_ACK_TIMEOUT', 'RESULT_ACK_TIMEOUT'])('renders localized communication guidance for %s', (code) => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: { metadata: {
        error_code: code,
        error_message: 'Original timeout diagnostic',
        failure_details: { count: 0, items: [] },
      } },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    expect(wrapper.get('.task-event-failure__summary').text()).toContain(en.ops.task.failureDetails.communicationTimeoutReason)
    expect(wrapper.get('.task-event-failure__remediation-list').text()).toContain(en.ops.task.failureDetails.communicationTimeoutResolution)
    expect(wrapper.get('details code').text()).toBe('Original timeout diagnostic')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
  })

  it('shows capacity guidance and collapsed raw errors even without file items', () => {
    const diagnostic = 'unable to write pack: no space left on device'
    const wrapper = mount(TaskEventFailureDetails, {
      props: { metadata: {
        error_message: 'Friendly capacity message',
        error_diagnostic: diagnostic,
        failure_details: {
          category: 'BACKUP_TARGET_STORAGE_FULL', count: 0, items: [],
          remediation: ['BACKUP_TARGET_STORAGE_FULL'],
        },
      } },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    expect(wrapper.text()).toContain('Backup target storage is full')
    expect(wrapper.text()).toContain('Free space, check the repository quota')
    expect(wrapper.get('details summary').text()).toBe('View original error')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    expect(wrapper.get('details code').text()).toBe(diagnostic)
    expect(wrapper.text()).not.toContain('unreadable')
    expect(wrapper.text()).not.toContain('Correct the listed source errors')
  })

  it('renders legacy offline errors once with collapsed original details', () => {
    const diagnostic = "{'source_ref_id': ['Agent source is offline.']}"
    const wrapper = mount(TaskEventFailureDetails, {
      props: { metadata: { error_code: 'BACKUP_PRECHECK_FAILED', error_message: diagnostic } },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    expect(wrapper.findAll('.task-event-failure__remediation')).toHaveLength(1)
    expect(wrapper.get('.task-event-failure__summary').text()).toContain('was offline')
    expect(wrapper.get('details').attributes('open')).toBeUndefined()
    expect(wrapper.get('details code').text()).toBe(diagnostic)
    expect(wrapper.text()).not.toContain('Showing 0 of')
  })

  it('shows actionable guidance and every structured failed file', async () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          source_path: 'E:\\ProgramData',
          failure_details: {
            category: 'source_file_locked',
            count: 2,
            remediation: ['enable_backup_policy', 'enable_skip_unreadable_files', 'use_vss'],
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
    expect(wrapper.find('.task-event-failure__remediation-list').exists()).toBe(true)
    const remediationItems = wrapper.findAll('.task-event-failure__remediation-list > li')
    expect(remediationItems).toHaveLength(3)
    expect(remediationItems[0].text()).toContain('First, enable the backup policy')
    expect(remediationItems[1].text()).toContain('skipped files will not be included in the snapshot')
    expect(wrapper.text()).toContain('View 2 affected items')
    expect(wrapper.text()).toContain('E:\\ProgramData\\Veeam\\PerfCache\\cpu\\LOCK')
    expect(wrapper.findAll('.task-event-failure__files li')).toHaveLength(2)
  })

  it('shows remediation before a collapsed affected-items list and omits redundant technical details', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        technicalDetail: 'Found 189 fatal error(s) while snapshotting.',
        metadata: {
          terminal_failure: { message: 'The backup could not process 189 source items.' },
          error_code: 'SOURCE_ITEMS_UNREADABLE',
          failure_details: {
            category: 'mixed_source_errors',
            count: 189,
            reported_count: 10,
            causes: [
              { code: 'permission_denied', count: 9 },
              { code: 'source_resource_busy', count: 1 },
              { code: 'snapshot_errors', count: 179 },
            ],
            remediation: ['enable_backup_policy', 'retry_backup'],
            items: Array.from({ length: 10 }, (_, index) => ({
              path: `file-${index}.txt`, error: 'permission denied',
            })),
          },
        },
      },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    const panel = wrapper.get('.task-event-failure')
    expect(panel.classes()).not.toContain('task-event-failure--mixed')
    expect(panel.get('.task-event-failure__summary--structured').text()).toContain('189 source items could not be processed')
    const remediation = panel.get('.task-event-failure__remediation')
    const affected = panel.get('.task-event-failure__files')
    expect(panel.element.compareDocumentPosition(remediation.element)).toBe(Node.DOCUMENT_POSITION_CONTAINED_BY | Node.DOCUMENT_POSITION_FOLLOWING)
    expect(remediation.element.compareDocumentPosition(affected.element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(affected.get('summary').text()).toContain('View 10 affected items')
    expect(affected.findAll('li')).toHaveLength(10)
    expect(affected.find('.task-event-failure__causes').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('items blocked by permissions')
    expect(wrapper.text()).not.toContain('snapshot errors')
    expect(wrapper.text()).not.toContain('Technical details')
  })

  it('keeps technical details for a structured failure without path samples', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        technicalDetail: 'Found 189 fatal error(s) while snapshotting.',
        metadata: {
          failure_details: {
            category: 'mixed_source_errors', count: 189, items: [],
            remediation: ['retry_backup'],
          },
        },
      },
      global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
    })
    expect(wrapper.find('.task-event-failure__files').exists()).toBe(false)
    expect(wrapper.get('.task-event-failure__technical summary').text()).toContain('Technical details')
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

  it('shows a concise restore permission cause, remediation, and optional diagnostics', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          error_code: 'RESTORE_TARGET_PERMISSION_DENIED',
          error_message: 'Permission denied while writing restore target "/tmp/existing".',
          target_path: '/tmp/existing',
          error_remediation: 'Verify target and parent permissions.',
          error_diagnostic: 'open /tmp/existing: permission denied',
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.text()).toContain('Permission denied while writing the restore target.')
    expect(wrapper.text()).toContain('Restore target:')
    expect(wrapper.text()).toContain('/tmp/existing')
    expect(wrapper.text()).toContain('How to resolve')
    expect(wrapper.text()).toContain('Change the restore directory to a location the restore service can write to.')
    expect(wrapper.text()).toContain('Under File conflict policy, select Skip duplicate files (keep source)')
    expect(wrapper.text()).toContain('Grant the service account used for restore permission to modify the target')
    expect(wrapper.findAll('.task-event-failure__remediation-list > li')).toHaveLength(3)
    expect(wrapper.text()).toContain('Technical details')
    expect(wrapper.text()).toContain('open /tmp/existing: permission denied')
  })

  it('does not render an empty affected-items disclosure when only the total is known', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          failure_details: {
            category: 'mixed_source_errors',
            total_count: 795,
            reported_count: 0,
            truncated: true,
            causes: [{ code: 'snapshot_errors', count: 795 }],
            items: [],
            remediation: ['retry_backup'],
          },
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.text()).toContain('Showing 0 reported items of 795; 795 items have no detailed record.')
    expect(wrapper.find('.task-event-failure__files').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('snapshot errors')
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
    expect(wrapper.text()).toContain('2 source items were skipped (files: 1; directories: 1; special entries: 0).')
    expect(wrapper.text()).toContain('View 2 skipped items')
    expect(wrapper.text()).toContain('E:\\ProgramData\\Veeam\\PerfCache\\cpu\\LOCK')
    expect(wrapper.text()).toContain('readdir: access denied')
  })

  it('shows terminal failure separately from skipped source items', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        terminalResolutions: ['Wait for the active operation to finish and retry the backup.'],
        metadata: {
          error_code: 'KOPIA_PROCESS_DIED',
          error_message: 'Backup processing failed: Device or resource busy.',
          skipped_details: {
            count: 1,
            directory_count: 1,
            items: [{ path: 'Documents and Settings', error: 'permission denied', item_type: 'directory' }],
          },
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.text()).toContain('Backup processing failed: Device or resource busy.')
    expect(wrapper.text()).toContain('Wait for the active operation to finish and retry the backup.')
    expect(wrapper.text()).toContain('1 source items were skipped')
    expect(wrapper.find('.task-event-failure--warning').exists()).toBe(false)
    expect(wrapper.find('.task-event-failure--mixed').exists()).toBe(true)
    expect(wrapper.find('.task-event-failure__summary--warning').exists()).toBe(true)
    expect(wrapper.find('.task-event-failure__summary--terminal').exists()).toBe(true)
    const summaries = wrapper.findAll('.task-event-failure__summary')
    expect(summaries.findIndex(item => item.classes('task-event-failure__summary--terminal')))
      .toBeLessThan(summaries.findIndex(item => item.classes('task-event-failure__summary--warning')))
  })

  it('can render only skipped warnings when the step owns terminal failure rendering', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        showTerminalFailure: false,
        metadata: {
          error_code: 'KOPIA_PROCESS_DIED',
          error_message: 'Backup processing failed: Device or resource busy.',
          skipped_details: {
            count: 1,
            directory_count: 1,
            items: [{ path: 'Documents and Settings', error: 'permission denied', item_type: 'directory' }],
          },
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.find('.task-event-failure__summary--terminal').exists()).toBe(false)
    expect(wrapper.find('.task-event-failure__summary--warning').exists()).toBe(true)
  })

  it('keeps technical details collapsible inside the directory failure box', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        technicalDetail: 'exit_code=1\nupload error: device or resource busy',
        metadata: {
          error_code: 'KOPIA_PROCESS_DIED',
          error_message: 'Backup processing failed: Device or resource busy.',
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.find('.task-event-failure__technical').exists()).toBe(true)
    expect(wrapper.find('.task-event-failure__technical pre').text()).toContain('upload error')
  })

  it('shows the affected path as a compact second line', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          terminal_failure: {
            message: 'The backup source reported Device or resource busy.',
            path: '/DumpStack.log.tmp',
          },
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })
    expect(wrapper.text()).toContain('Affected path: /DumpStack.log.tmp')
  })

  it('shows a copy-ready exact exclusion rule for a known source Busy path', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        terminalResolutions: ['Review the source and target repository.'],
        metadata: {
          terminal_failure: {
            message: 'Backup processing failed: Device or resource busy. The affected path could not be determined.',
            side: 'source',
            confidence: 'exact',
            path: '/swapfile.sys',
            filter_rule: '/swapfile.sys',
          },
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })
    expect(wrapper.get('.task-event-failure__summary--terminal').text())
      .toContain('The backup source reported Device or resource busy.')
    expect(wrapper.text()).toContain('Affected path: /swapfile.sys')
    expect(wrapper.get('.task-event-failure__filter-rule').text()).toBe('/swapfile.sys')
    expect(wrapper.get('.task-event-failure__copy-rule').attributes('aria-label'))
      .toBe('Copy file filter rule')
    expect(wrapper.text()).not.toContain('Review the source and target repository.')
    expect(wrapper.text()).not.toContain('path could not be determined')
  })

  it('shows only skipped counts for a Finalize summary event', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          skipped_item_count: 6,
          skipped_file_count: 4,
          skipped_directory_count: 1,
          skipped_special_count: 1,
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.text()).toContain('6 source items were skipped (files: 4; directories: 1; special entries: 1).')
    expect(wrapper.find('details').exists()).toBe(false)
  })

  it('caps legacy skipped-item payloads at ten visible items', () => {
    const wrapper = mount(TaskEventFailureDetails, {
      props: {
        metadata: {
          skipped_details: {
            count: 20,
            reported_count: 20,
            truncated: false,
            items: Array.from({ length: 20 }, (_, index) => ({
              path: `cache/item-${index}.tmp`,
              error: 'sharing violation',
            })),
          },
        },
      },
      global: {
        plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      },
    })

    expect(wrapper.text()).toContain('View 10 skipped items')
    expect(wrapper.text()).toContain('Showing 10 reported skipped items of 20; 10 items have no detailed record.')
    expect(wrapper.findAll('.task-event-failure__files li')).toHaveLength(10)
    expect(wrapper.text()).toContain('cache/item-9.tmp')
    expect(wrapper.text()).not.toContain('cache/item-10.tmp')
  })
})
