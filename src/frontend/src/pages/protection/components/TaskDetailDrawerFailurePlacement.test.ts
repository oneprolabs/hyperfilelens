// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskDetailDrawer from './TaskDetailDrawer.vue'
import { getTask, listTaskEvents, type TaskRow, type TaskEventRow } from '../../../lib/taskApi'

vi.mock('../../../lib/taskApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/taskApi')>()),
  getTask: vi.fn(),
  listTaskEvents: vi.fn(),
}))
vi.mock('../../../composables/useDrawerTableMaxHeight', () => ({
  useDrawerTableMaxHeight: () => ({ tableMaxHeight: ref(400), containerRef: ref(null) }),
}))

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  missingWarn: false,
  fallbackWarn: false,
  messages: { en: { ops: { task: { failureDetails: {
    failureTitle: 'Task failed',
    unattributedFailure: 'Could not determine which step failed. Review the task diagnostics.',
    suggestions: 'Suggested next steps',
  } } } } },
})

const steps = [
  { id: 1, step_index: 1, step_name: 'kopia_snapshot', status: 'success', progress: 100 },
  { id: 2, step_index: 2, step_name: 'finalize_snapshot', status: 'success', progress: 100 },
]
const task = {
  id: 1,
  organization_id: 1,
  task_uuid: 'backup-task',
  task_type: 'backup',
  display_name: 'Backup',
  status: 'success',
  current_step: 'finalize_snapshot',
  progress: 100,
  retry_count: 0,
  recovery_attempt: 0,
  trigger_type: 'manual',
  steps,
  resources: [],
  recent_events: [],
  error_details: {
    version: 1,
    severity: 'warning',
    outcome: 'warning',
    summary: 'Task completed with warnings.',
    reasons: [],
    suggestions: [{ code: 'review_backup_diagnostics', detail: 'Review skipped items.' }],
    skipped_items: { count: 264 },
    task_uuid: 'backup-task',
  },
} satisfies Partial<TaskRow> as TaskRow

function mountDrawer() {
  return mount(TaskDetailDrawer, {
    props: { modelValue: true, taskUuid: 'backup-task' },
    global: {
      plugins: [i18n],
      directives: { loading: {}, 'table-column-resize': {}, 'table-overflow-title': {} },
      stubs: {
        ElDrawer: { template: '<div><slot name="header" /><slot /></div>' },
        ElButton: true,
        ElTag: true,
        ElTabs: { template: '<div><slot /></div>' },
        ElTabPane: { template: '<div><slot /></div>' },
        ElTable: true,
        ElTableColumn: true,
        ElAlert: true,
        ElTooltip: true,
        ElEmpty: true,
        ElSkeleton: true,
      },
    },
  })
}

beforeEach(() => {
  vi.mocked(getTask).mockResolvedValue(task)
  vi.mocked(listTaskEvents).mockResolvedValue({ count: 0, results: [] })
})

afterEach(() => vi.clearAllMocks())

describe('TaskDetailDrawer failure placement', () => {
  it('does not put a successful backup warning into a red Task failed panel', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    wrapper.unmount()
  })

  it('attaches a failure fallback only to the step actually marked failed', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
        failed_step: 'finalize_snapshot', // stale hint must not override step status
      },
    })
    const wrapper = mountDrawer()
    await flushPromises()
    const cards = wrapper.findAll('.hfl-task-drawer__step-card')
    expect(cards[0].findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(1)
    expect(cards[1].findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(0)
    wrapper.unmount()
  })

  it('uses one unattributed task fallback when no failed step or directory event exists', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
      },
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__step-card .hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(1)
    expect(wrapper.text()).toContain('Could not determine which step failed.')
    wrapper.unmount()
  })

  it('does not duplicate a directory-specific failure at the step or task level', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
      },
    })
    const event: TaskEventRow = {
      id: 3,
      step_id: 1,
      seq: 3,
      level: 'ERROR',
      message: 'Directory backup failed',
      metadata: { error_code: 'KOPIA_PROCESS_DIED', error_message: 'Source read failed' },
    }
    vi.mocked(listTaskEvents).mockResolvedValue({ count: 1, results: [event] })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(0)
    expect(wrapper.findAll('.task-event-failure__terminal-box')).toHaveLength(1)
    wrapper.unmount()
  })

  it('does not duplicate an explicit non-directory error event', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
      },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 4,
        step_id: 1,
        seq: 4,
        level: 'ERROR',
        message: 'Repository connection failed',
        metadata: { error_code: 'REPOSITORY_UNAVAILABLE', error_message: 'Repository unavailable' },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(0)
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(1)
    wrapper.unmount()
  })

  it('hides stale skipped samples when a failed task explicitly has zero skipped items', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      result_payload: { skipped_item_count: 0, skipped_file_count: 0, skipped_directory_count: 0 },
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
      },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 5,
        step_id: 1,
        seq: 5,
        level: 'ERROR',
        message: 'Directory backup failed',
        metadata: {
          error_code: 'SOURCE_ITEMS_UNREADABLE',
          failure_details: {
            count: 189,
            items: [{ path: '/DumpStack.log.tmp', error: 'device or resource busy' }],
          },
          skipped_details: {
            count: 189,
            items: [{ path: '/DumpStack.log.tmp', error: 'device or resource busy' }],
          },
        },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.text()).not.toContain('189 source items were skipped')
    expect(wrapper.findAll('.task-event-failure__summary--warning')).toHaveLength(0)
    expect(wrapper.findAll('.task-event-failure__summary:not(.task-event-failure__summary--warning)')).not.toHaveLength(0)
    wrapper.unmount()
  })
})
