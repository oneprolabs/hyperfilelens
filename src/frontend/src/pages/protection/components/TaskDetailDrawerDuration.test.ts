// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { ref } from 'vue'
import { createI18n } from 'vue-i18n'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import TaskDetailDrawer from './TaskDetailDrawer.vue'
import { getTask, listTaskEvents, type TaskRow } from '../../../lib/taskApi'

vi.mock('../../../lib/taskApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../../lib/taskApi')>()),
  getTask: vi.fn(),
  listTaskEvents: vi.fn(),
}))
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
  } } } },
})

function mountDrawer() {
  return mount(TaskDetailDrawer, {
    props: { modelValue: true, taskUuid: 'running-1' },
    global: {
      plugins: [i18n],
      directives: { loading: {}, 'table-column-resize': {}, 'table-overflow-title': {} },
      stubs: {
        ElDrawer: { template: '<div><slot name="header" /><slot /></div>' },
        ElButton: true,
        ElTag: true,
        ElTabs: true,
        ElTabPane: true,
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
  vi.useFakeTimers()
  vi.setSystemTime(new Date(start))
  vi.mocked(getTask).mockResolvedValue({ ...running })
  vi.mocked(listTaskEvents).mockResolvedValue({ count: 0, results: [] })
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('TaskDetailDrawer live duration', () => {
  it('updates locally and checks server status only every two minutes', async () => {
    const wrapper = mountDrawer()
    await flushPromises()
    expect(wrapper.text()).toContain('Total Duration')
    expect(wrapper.get('.hfl-task-drawer__time-value--strong').text()).toBe('00:00:00')

    await vi.advanceTimersByTimeAsync(10_000)
    expect(wrapper.get('.hfl-task-drawer__time-value--strong').text()).toBe('00:00:10')
    expect(getTask).toHaveBeenCalledTimes(1)

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
    let resolvePoll!: (task: TaskRow) => void
    vi.mocked(getTask).mockImplementationOnce(() => new Promise((resolve) => { resolvePoll = resolve }))
    await vi.advanceTimersByTimeAsync(120_000)
    expect(getTask).toHaveBeenCalledTimes(2)

    vi.mocked(getTask).mockResolvedValueOnce({
      ...running,
      task_uuid: 'new-task',
      status: 'waiting',
      started_at: null,
    })
    await wrapper.setProps({ taskUuid: 'new-task' })
    await flushPromises()
    resolvePoll({ ...running, status: 'success', finished_at: new Date(Date.parse(start) + 119_000).toISOString() })
    await flushPromises()
    expect(wrapper.emitted('task-updated')).toBeUndefined()
    expect(wrapper.text()).not.toContain('00:01:59')
    await vi.advanceTimersByTimeAsync(120_000)
    expect(getTask).toHaveBeenCalledTimes(3)
    wrapper.unmount()
  })
})
