// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchBackupTaskRuntime } from '../../../lib/protectionBackupTaskApi'
import TaskDetailDrawer from './TaskDetailDrawer.vue'
import { getTask, listTaskEvents, type TaskRow } from '../../../lib/taskApi'

vi.mock('../../../lib/taskApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/taskApi')>()),
  getTask: vi.fn(),
  listTaskEvents: vi.fn(),
}))
vi.mock('../../../lib/protectionBackupTaskApi', () => ({ cancelProtectionBackupTask: vi.fn(), fetchBackupTaskRuntime: vi.fn() }))
vi.mock('../../../composables/useDrawerTableMaxHeight', () => ({
  useDrawerTableMaxHeight: () => ({ tableMaxHeight: ref(400), containerRef: ref(null) }),
}))

const start = '2026-09-29T07:58:49.000Z'
const running = {
  task_uuid: 'running-1',
  status: 'running',
  started_at: start,
  finished_at: null,
  task_type: 'backup',
  display_name: 'Backup',
  progress: 0,
  retry_count: 0,
  recovery_attempt: 0,
  trigger_type: 'manual',
  transfer_progress: {
    processed_entry_count: 1604089,
    hashed_count: 8,
    cached_count: 1604081,
  },
  resources: [],
  steps: [],
  recent_events: [],
} as TaskRow

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  missingWarn: false,
  fallbackWarn: false,
  messages: { en: { ops: { task: {
    emptyMark: '—',
    totalDuration: 'Total Duration',
    startTime: 'Start Time',
    endTime: 'End Time',
    retryCountValue: '{count} times',
    retryCount: 'Retries',
    executionHistory: 'Execution History',
    earlierRecords: 'Earlier records',
    triggerType: { system: 'System', manual: 'Manual', retry: 'Retry' },
  } } } },
})

function mountDrawer(extraProps: { fetchTask?: typeof getTask } = {}) {
  return mount(TaskDetailDrawer, {
    props: { modelValue: true, taskUuid: 'running-1', ...extraProps },
    global: {
      plugins: [i18n],
      directives: { loading: {}, 'table-column-resize': {}, 'table-overflow-title': {} },
      stubs: {
        ElDrawer: { template: '<div><slot name="header" /><slot /></div>' },
        ElButton: { template: '<button><slot /></button>' },
        ElTag: { template: '<span><slot /></span>' },
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

function historyEntry(uuid: string, status = 'failed') {
  return { task_uuid: uuid, status, started_at: start, finished_at: start, created_at: start }
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(start))
  vi.mocked(getTask).mockResolvedValue({ ...running })
  vi.mocked(fetchBackupTaskRuntime).mockResolvedValue({ status: 'running', progress: 0 })
  vi.mocked(listTaskEvents).mockResolvedValue({ count: 0, results: [] })
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('TaskDetailDrawer live duration', () => {
  it('keeps loaded history and its cursor while polling updates execution outcomes', async () => {
    const latest: TaskRow = {
      ...running, task_type: 'repository_operation',
      execution_history: [historyEntry('middle'), historyEntry('running-1', 'running')],
      execution_history_next_cursor: 10,
    }
    const fetchTask = vi.fn<typeof getTask>()
      .mockResolvedValueOnce(latest)
      .mockResolvedValueOnce({
        ...latest, execution_history: [historyEntry('oldest'), historyEntry('middle')],
        execution_history_next_cursor: 5,
      })
      .mockResolvedValueOnce({
        ...latest, status: 'success',
        execution_history: [historyEntry('middle'), historyEntry('running-1', 'success')],
      })
      .mockResolvedValueOnce({
        ...latest, execution_history: [historyEntry('ancient')],
        execution_history_next_cursor: null,
      })
    const wrapper = mountDrawer({ fetchTask })
    await flushPromises()
    const section = wrapper.get('.hfl-task-drawer__execution-history')
    await section.find('button').trigger('click')
    await flushPromises()
    expect(section.findAll('li')).toHaveLength(3)
    await vi.advanceTimersByTimeAsync(120_000)
    await flushPromises()
    expect(fetchTask).toHaveBeenCalledTimes(3)
    expect(section.findAll('li')).toHaveLength(3)
    expect(section.findAllComponents({ name: 'TaskStatusTag' }).map(tag => tag.props('status')))
      .toEqual(['failed', 'failed', 'success'])
    await section.find('button').trigger('click')
    await flushPromises()
    expect(fetchTask).toHaveBeenLastCalledWith('running-1', undefined, 5)
    expect(section.findAll('li')).toHaveLength(4)
    wrapper.unmount()
  })

  it('accepts an older page across a same-task status refresh without reverting fresh outcomes', async () => {
    const latest: TaskRow = {
      ...running, task_type: 'repository_operation',
      execution_history: [historyEntry('middle'), historyEntry('running-1', 'running')],
      execution_history_next_cursor: 10,
    }
    let resolvePage!: (page: TaskRow) => void
    const fetchTask = vi.fn<typeof getTask>()
      .mockResolvedValueOnce(latest)
      .mockImplementationOnce(() => new Promise(resolve => { resolvePage = resolve }))
      .mockResolvedValueOnce({
        ...latest, status: 'success',
        execution_history: [historyEntry('middle'), historyEntry('running-1', 'success')],
      })
    const wrapper = mountDrawer({ fetchTask })
    await flushPromises()
    const section = wrapper.get('.hfl-task-drawer__execution-history')
    await section.find('button').trigger('click')
    await vi.advanceTimersByTimeAsync(120_000)
    await flushPromises()
    resolvePage({
      ...latest,
      execution_history: [historyEntry('oldest'), historyEntry('running-1', 'running')],
      execution_history_next_cursor: null,
    })
    await flushPromises()
    expect(section.findAll('li')).toHaveLength(3)
    expect(section.findAllComponents({ name: 'TaskStatusTag' }).map(tag => tag.props('status')))
      .toEqual(['failed', 'failed', 'success'])
    wrapper.unmount()
  })

  it('preserves older records through manual refresh and repository cancellation polling', async () => {
    const latest: TaskRow = {
      ...running, task_type: 'repository_operation', status: 'pending',
      repository_cancellation: { supported: true, requested_at: start },
      execution_history: [historyEntry('middle'), historyEntry('running-1', 'pending')],
      execution_history_next_cursor: 10,
    }
    const fetchTask = vi.fn<typeof getTask>()
      .mockResolvedValueOnce(latest)
      .mockResolvedValueOnce({
        ...latest, execution_history: [historyEntry('oldest')], execution_history_next_cursor: null,
      })
      .mockResolvedValueOnce(latest)
      .mockResolvedValueOnce({
        ...latest, status: 'cancelled',
        execution_history: [], execution_history_next_cursor: null,
      })
    const wrapper = mountDrawer({ fetchTask })
    await flushPromises()
    const section = wrapper.get('.hfl-task-drawer__execution-history')
    await section.find('button').trigger('click')
    await flushPromises()
    await wrapper.get('button.hfl-drawer-header-action').trigger('click')
    await flushPromises()
    expect(section.findAll('li')).toHaveLength(3)
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(fetchTask).toHaveBeenCalledTimes(4)
    expect(section.findAll('li')).toHaveLength(3)
    expect(section.findAllComponents({ name: 'TaskStatusTag' }).map(tag => tag.props('status')))
      .toEqual(['failed', 'failed', 'cancelled'])
    expect(section.findAll('button').some(button => button.text() === 'Earlier records')).toBe(false)
    wrapper.unmount()
  })

  it('resets history on close and ignores a late page after reopening the same task', async () => {
    const latest: TaskRow = {
      ...running, task_type: 'repository_operation', status: 'success',
      execution_history: [historyEntry('middle'), historyEntry('running-1')],
      execution_history_next_cursor: 10,
    }
    let resolvePage!: (page: TaskRow) => void
    const fetchTask = vi.fn<typeof getTask>()
      .mockResolvedValueOnce(latest)
      .mockImplementationOnce(() => new Promise(resolve => { resolvePage = resolve }))
      .mockResolvedValueOnce(latest)
    const wrapper = mountDrawer({ fetchTask })
    await flushPromises()
    await wrapper.get('.hfl-task-drawer__execution-history button').trigger('click')
    await wrapper.setProps({ modelValue: false })
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
    resolvePage({
      ...latest, execution_history: [historyEntry('oldest')], execution_history_next_cursor: null,
    })
    await flushPromises()
    const section = wrapper.get('.hfl-task-drawer__execution-history')
    expect(section.findAll('li')).toHaveLength(2)
    expect(section.findAll('button').some(button => button.text() === 'Earlier records')).toBe(true)
    wrapper.unmount()
  })

  it('loads older records through the injected detail API without changing the active task', async () => {
    const entry = (uuid: string) => ({
      task_uuid: uuid, status: 'failed', started_at: start, finished_at: start, created_at: start,
    })
    const latest = {
      ...running, task_type: 'repository_operation', status: 'success',
      execution_history: [entry('middle'), entry('running-1')],
      execution_history_next_cursor: 10,
    }
    const fetchTask = vi.fn<typeof getTask>()
      .mockResolvedValueOnce(latest)
      .mockResolvedValueOnce({
        ...latest, execution_history: [entry('oldest'), entry('middle')],
        execution_history_next_cursor: null,
      })
    const wrapper = mountDrawer({ fetchTask })
    await flushPromises()
    const section = wrapper.get('.hfl-task-drawer__execution-history')
    await section.find('button').trigger('click')
    await flushPromises()
    expect(fetchTask).toHaveBeenLastCalledWith('running-1', undefined, 10)
    expect(getTask).not.toHaveBeenCalled()
    expect(section.findAll('li')).toHaveLength(3)
    expect(section.findAll('li').map(row => row.findComponent({ name: 'TaskStatusTag' }).props('status')))
      .toEqual(['failed', 'failed', 'failed'])
    expect(section.findAll('button').some(button => button.text() === 'Earlier records')).toBe(false)
    await section.find('li button').trigger('click')
    expect(wrapper.emitted('open-task')).toEqual([['oldest']])
    wrapper.unmount()
  })

  it('ignores stale history pages after navigation and prevents duplicate page requests', async () => {
    const entry = (uuid: string) => ({
      task_uuid: uuid, status: 'failed', started_at: start, finished_at: start, created_at: start,
    })
    const latest = {
      ...running, task_type: 'repository_operation', status: 'success',
      execution_history: [entry('running-1')], execution_history_next_cursor: 10,
    }
    let resolvePage!: (page: TaskRow) => void
    const fetchTask = vi.fn<typeof getTask>()
      .mockResolvedValueOnce(latest)
      .mockImplementationOnce(() => new Promise(resolve => { resolvePage = resolve }))
      .mockResolvedValueOnce({
        ...latest, task_uuid: 'other-task', execution_history: [entry('other-task')],
        execution_history_next_cursor: null,
      })
    const wrapper = mountDrawer({ fetchTask })
    await flushPromises()
    const button = wrapper.get('.hfl-task-drawer__execution-history button')
    await button.trigger('click')
    await button.trigger('click')
    expect(fetchTask).toHaveBeenCalledTimes(2)
    await wrapper.setProps({ taskUuid: 'other-task' })
    await flushPromises()
    resolvePage({ ...latest, execution_history: [entry('oldest')], execution_history_next_cursor: null })
    await flushPromises()
    expect(wrapper.find('.hfl-task-drawer__execution-history').exists()).toBe(false)
    expect(wrapper.get('.hfl-task-drawer__uuid').text()).toContain('other-task')
    wrapper.unmount()
  })

  it('shows linked execution times and results without retry counters or badges', async () => {
    const history = [
      { task_uuid: 'original', status: 'failed', started_at: start, finished_at: start, created_at: start },
      { task_uuid: 'running-1', status: 'success', started_at: start, finished_at: start, created_at: start },
    ]
    vi.mocked(getTask).mockResolvedValue({
      ...running,
      task_type: 'repository_operation',
      trigger_type: 'system',
      status: 'success',
      finished_at: start,
      retry_count: 2,
      recovery_attempt: 1,
      execution_history: history,
    })
    const wrapper = mountDrawer()
    await flushPromises()
    const section = wrapper.get('.hfl-task-drawer__execution-history')
    expect(section.text()).toContain('Execution History')
    expect(section.findAll('li')).toHaveLength(2)
    expect(section.findAllComponents({ name: 'TaskStatusTag' }).map(tag => tag.props('status')))
      .toEqual(['failed', 'success'])
    expect(wrapper.text()).not.toContain('Retries')
    expect(wrapper.text()).not.toContain('2 times')
    expect(wrapper.get('.hfl-task-drawer__metric-tags').text()).toContain('System')
    expect(wrapper.get('.hfl-task-drawer__metric-tags').text()).not.toContain('Retry')
    expect(section.text()).not.toMatch(/Retry|Attempt|Replacement/i)
    await section.find('button').trigger('click')
    expect(wrapper.emitted('open-task')).toEqual([['original']])
    wrapper.unmount()
  })

  it('does not add an execution history section for an unrelated single run', async () => {
    vi.mocked(getTask).mockResolvedValue({
      ...running,
      task_type: 'repository_operation',
      execution_history: [{
        task_uuid: running.task_uuid, status: 'running', started_at: start,
        finished_at: null, created_at: start,
      }],
    })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.find('.hfl-task-drawer__execution-history').exists()).toBe(false)
    wrapper.unmount()
  })

  it('excludes the delayed orphan retry queue from execution-step duration', async () => {
    const at = (seconds: number) => new Date(Date.parse(start) + seconds * 1000).toISOString()
    const names = ['prepare_repository_operation', 'verify_repository_owner', 'run_repository_operation', 'finalize_repository_operation']
    vi.mocked(getTask).mockResolvedValue({
      ...running,
      task_type: 'repository_operation',
      operation_type: 'snapshot.reconcile',
      trigger_type: 'system',
      recovery_attempt: 1,
      status: 'success',
      started_at: at(60),
      finished_at: at(62),
      steps: names.map((step_name, index) => ({
        id: index + 1, step_index: index + 1, step_name, status: 'success', progress: 100,
      })),
    })
    const events = [
      { step_id: 1, message: 'Task created', created_at: at(0) },
      { step_id: 1, message: 'Step prepare_repository_operation running', created_at: at(60) },
      { step_id: 2, message: 'Step verify_repository_owner running', created_at: at(60) },
      { step_id: 3, message: 'Step run_repository_operation running', created_at: at(60) },
      { step_id: 4, message: 'Step finalize_repository_operation success', created_at: at(62) },
    ].map((event, index) => ({ ...event, id: index + 1, seq: index + 1, level: 'INFO' }))
    vi.mocked(listTaskEvents).mockResolvedValue({ count: events.length, results: events })
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.get('.hfl-task-drawer__time-value--strong').text()).toBe('00:00:02')
    expect(wrapper.findAll('.hfl-task-drawer__step-duration').map(item => item.text())).toEqual(['0s', '0s', '2s', '0s'])
    wrapper.unmount()
  })

  it('updates locally and polls lightweight backup runtime every five seconds', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.text()).toContain('Total Duration')
    expect(wrapper.find('.backup-run-counters').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Backup Counters')
    expect(wrapper.text()).not.toContain('backupCountersTitle')
    expect(wrapper.get('.hfl-task-drawer__time-value--strong').text()).toBe('00:00:00')

    await vi.advanceTimersByTimeAsync(10_000)
    expect(wrapper.get('.hfl-task-drawer__time-value--strong').text()).toBe('00:00:10')
    expect(getTask).toHaveBeenCalledTimes(1)

    expect(fetchBackupTaskRuntime).toHaveBeenCalledTimes(2)
    vi.mocked(fetchBackupTaskRuntime).mockResolvedValueOnce({ status: 'success' })
    vi.mocked(getTask).mockResolvedValueOnce({
      ...running,
      status: 'success',
      finished_at: new Date(Date.parse(start) + 119_000).toISOString(),
    })
    await vi.advanceTimersByTimeAsync(110_000)
    await flushPromises()
    expect(getTask).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.hfl-task-drawer__time-value--strong').text()).toBe('00:01:59')

    await vi.advanceTimersByTimeAsync(120_000)
    expect(getTask).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.hfl-task-drawer__time-value--strong').text()).toBe('00:01:59')
    wrapper.unmount()
  })

  it('keeps a day-long duration compact while exposing the exact seconds on hover', async () => {
    vi.setSystemTime(new Date(Date.parse(start) + (86_400 + 2 * 3600 + 9 * 60 + 31) * 1000))
    const wrapper = mountDrawer()
    await flushPromises()
    const duration = wrapper.get('.hfl-task-drawer__time-value--strong')
    expect(duration.text()).toBe('1d 02:09')
    expect(duration.attributes('title')).toBe('1d 02:09:31')
    await vi.advanceTimersByTimeAsync(1000)
    expect(duration.text()).toBe('1d 02:09')
    expect(duration.attributes('title')).toBe('1d 02:09:32')
    wrapper.unmount()
  })

  it('stops both timers when the drawer closes', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    await wrapper.setProps({ modelValue: false })
    await vi.advanceTimersByTimeAsync(120_000)
    expect(getTask).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('does not apply a late status response after the drawer switches tasks', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    let resolvePoll!: (task: { status: string }) => void
    vi.mocked(fetchBackupTaskRuntime).mockImplementationOnce(() => new Promise((resolve) => { resolvePoll = resolve }))
    await vi.advanceTimersByTimeAsync(5000)
    expect(getTask).toHaveBeenCalledTimes(1)

    vi.mocked(getTask).mockResolvedValueOnce({
      ...running,
      task_uuid: 'new-task',
      status: 'waiting',
      started_at: null,
    })
    await wrapper.setProps({ taskUuid: 'new-task' })
    await flushPromises()
    resolvePoll({ status: 'success' })
    await flushPromises()
    expect(wrapper.emitted('task-updated')).toBeUndefined()
    expect(wrapper.text()).not.toContain('00:01:59')
    await vi.advanceTimersByTimeAsync(120_000)
    expect(getTask).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })
})
