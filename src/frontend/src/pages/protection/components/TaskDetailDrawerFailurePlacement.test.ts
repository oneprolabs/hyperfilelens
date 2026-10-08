// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { i18n, registerLocale } from '../../../i18n'
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

for (const locale of ['zh-hans', 'es']) {
  registerLocale(locale, JSON.parse(readFileSync(resolve(process.cwd(), `../../language-packs/packs/${locale}/frontend/messages.json`), 'utf8')))
}

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
  i18n.global.locale.value = 'en'
  vi.mocked(getTask).mockResolvedValue(task)
  vi.mocked(listTaskEvents).mockResolvedValue({ count: 0, results: [] })
})

afterEach(() => {
  i18n.global.locale.value = 'en'
  vi.clearAllMocks()
})

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
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    expect(wrapper.findAll('.task-event-failure__terminal-box')).toHaveLength(1)
    wrapper.unmount()
  })

  it('shows one standard panel and controller-specific guidance for a recovered maintenance failure', async () => {
    const message = 'Controller repository maintenance lost its execution heartbeat after a control-plane interruption.'
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      task_type: 'repository_operation',
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        summary: 'Task failed.',
        suggestions: [{ code: 'recover_controller_interruption', detail: 'recover_controller_interruption' }],
      },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 6, step_id: 1, seq: 6, level: 'ERROR',
        message: 'Task finished with status failed',
        metadata: { error_code: 'CONTROL_PLANE_RESTART_INTERRUPTED', error_message: message },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    expect(wrapper.findAll('.task-event-failure__terminal-box')).toHaveLength(1)
    expect(wrapper.get('.task-event-failure__summary--terminal').text()).toBe(message)
    expect(wrapper.get('.task-event-failure__remediation-list').text()).toContain('Scheduled maintenance will retry automatically')
    expect(wrapper.get('.task-event-failure__remediation-list').text()).not.toContain('repository configuration')
    expect(wrapper.text()).not.toContain('Review the repository configuration')
    expect(wrapper.find('.task-event-failure__technical').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps the generic fallback when an error message has no standard error code', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: { ...task.error_details!, severity: 'error', outcome: 'failed' },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 7, step_id: 1, seq: 7, level: 'ERROR',
        message: 'Task finished with status failed',
        metadata: { error_message: 'Unclassified failure' },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(1)
    expect(wrapper.find('.task-event-failure').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each(
    ['en', 'zh-hans', 'es'].flatMap(locale =>
      ['proxy', 'backup_host', 'host'].map(role => ({ locale, role })),
    ),
  )('localizes the $role connection failure in $locale and retains raw diagnostics', async ({ locale, role }) => {
    i18n.global.locale.value = locale
    const rawError = 'agent websocket is not routable'
    const reasonCode = `repository_${role}_unreachable`
    const suggestionCode = `reconnect_repository_${role}`
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      task_type: 'repository_operation',
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: {
        ...task.error_details!,
        severity: 'error',
        outcome: 'failed',
        reasons: [{ code: reasonCode, detail: 'Server-side English fallback' }],
        suggestions: [{ code: suggestionCode, detail: 'Server-side English fallback' }],
      },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 9, step_id: 1, seq: 9, level: 'ERROR',
        message: 'Task finished with status failed',
        metadata: { error_code: 'REPOSITORY_OPERATION_FAILED', error_message: rawError },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel')).toHaveLength(0)
    expect(wrapper.get('.task-event-failure__summary--terminal').text())
      .toBe(i18n.global.t(`ops.task.failureDetails.reason.${reasonCode}`))
    const resolution = wrapper.get('.task-event-failure__remediation-list').text()
    expect(resolution).toBe(i18n.global.t(`ops.task.failureDetails.suggestion.${suggestionCode}`))
    expect(resolution).not.toContain('Agent')
    expect(wrapper.get('.task-event-failure__summary--terminal').text()).not.toContain('Agent')
    expect(resolution).not.toContain('repository configuration')
    expect(wrapper.text()).not.toContain('Server-side English fallback')
    expect(wrapper.get('.task-event-failure__technical pre').text()).toBe(rawError)
    expect(wrapper.get('.task-event-failure__technical').attributes('open')).toBeUndefined()
    wrapper.unmount()
  })

  it('anchors a task-level fallback when an event has only an error code', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...task,
      status: 'failed',
      steps: [{ ...steps[0], status: 'failed' }, steps[1]],
      error_details: { ...task.error_details!, severity: 'error', outcome: 'failed' },
    })
    vi.mocked(listTaskEvents).mockResolvedValue({
      count: 1,
      results: [{
        id: 8, step_id: 1, seq: 8, level: 'ERROR',
        message: 'Task finished with status failed',
        metadata: { error_code: 'UNKNOWN_FAILURE' },
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.findAll('.hfl-task-drawer__event-failure-panel--stacked')).toHaveLength(1)
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
