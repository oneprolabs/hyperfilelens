// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { en } from '../../../locales/en'
import CopilotThinkingTimeline, { type TimelineStep } from './CopilotThinkingTimeline.vue'

const mounted: ReturnType<typeof mount>[] = []
afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount())
  vi.useRealTimers()
})

function plan(steps: Record<string, unknown>[], observedAt?: string, revision = 1): TimelineStep {
  return { message: 'plan.updated', eventType: 'plan.updated', observedAt, payload: { steps, revision } }
}

function render(steps: TimelineStep[], live = false) {
  const wrapper = mount(CopilotThinkingTimeline, {
    props: { steps, live },
    global: { plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })] },
  })
  mounted.push(wrapper)
  return wrapper
}

describe('Copilot plan progress', () => {
  it('numbers nested steps without altering their titles or inventing history durations', () => {
    const wrapper = render([plan([
      { id: 'one', title: 'Search', status: 'completed' },
      { id: 'child', parent_id: 'one', title: 'Read', status: 'completed' },
      { id: 'two', title: 'Answer', status: 'completed' },
    ])])
    const rows = wrapper.findAll('.copilot-runtime-card__row--plan')
    expect(rows.map((row) => row.find('strong').text())).toEqual(['1. Search', '1.1. Read', '2. Answer'])
    expect(wrapper.find('.copilot-timeline-time').exists()).toBe(false)
  })

  it('ticks live durations, freezes completed steps and discards stale plan revisions', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-23T00:00:00Z'))
    const steps = [plan([{ id: 'one', title: 'Search', status: 'in_progress' }], '2026-11-23T00:00:00Z')]
    const wrapper = render(steps, true)
    await vi.advanceTimersByTimeAsync(5_000)
    expect(wrapper.find('.copilot-timeline-time').text()).toBe('5s')
    await wrapper.setProps({ steps: [
      ...steps,
      plan([{ id: 'one', title: 'Search', status: 'completed' }], '2026-11-23T00:00:05Z', 2),
      plan([{ id: 'one', title: 'Search', status: 'in_progress' }], '2026-11-23T00:00:06Z', 1),
    ] })
    await vi.advanceTimersByTimeAsync(5_000)
    expect(wrapper.find('.copilot-timeline-time').text()).toBe('5s')
    expect(wrapper.find('.copilot-runtime-card__status').classes()).toContain('is-completed')
    await wrapper.setProps({ live: false })
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not let a delegated assistant revision suppress parent progress', () => {
    const wrapper = render([
      plan([{ id: 'parent', title: 'Search', status: 'in_progress' }], undefined, 1),
      { ...plan([{ id: 'child', title: 'Read', status: 'completed' }], undefined, 20), assistantName: 'Reader' },
      plan([{ id: 'parent', title: 'Search', status: 'completed' }], undefined, 2),
    ])
    const row = wrapper.findAll('.copilot-runtime-card__row--plan')[0]
    expect(row.find('.copilot-runtime-card__status').classes()).toContain('is-completed')
  })

  it.each(['assistant', 'delegated-run'])('isolates same-ID plan timings by %s', (identity) => {
    const event = (child: boolean, status: string, second: string, revision: number): TimelineStep => ({
      ...plan([{ id: 'step-1', title: child ? 'Child task' : 'Parent task', status }],
        `2026-11-23T00:00:${second}Z`, revision),
      assistantName: child || identity === 'delegated-run' ? 'Reader' : undefined,
      delegatedRunUuid: identity === 'delegated-run' ? (child ? 'child-2' : 'child-1') : undefined,
    })
    const wrapper = render([
      event(false, 'in_progress', '00', 1),
      event(true, 'in_progress', '05', 1),
      event(true, 'completed', '08', 20),
      event(false, 'completed', '20', 2),
    ])
    const rows = wrapper.findAll('.copilot-runtime-card__row--plan')
    expect(rows.map((row) => row.find('strong').text())).toEqual(['1. Parent task', '2. Child task'])
    expect(rows.map((row) => row.find('.copilot-timeline-time').text())).toEqual(['20s', '3s'])
    expect(rows.every((row) => row.find('.copilot-runtime-card__status').classes().includes('is-completed'))).toBe(true)
  })

  it('keeps same-ID parent/child references within each history execution source', () => {
    const steps = [
      { id: 'root', title: 'Search', status: 'completed' },
      { id: 'child', parent_id: 'root', title: 'Read', status: 'completed' },
    ]
    const wrapper = render([
      { event_type: 'plan.updated', assistant_name: 'Reader', delegated_task: 'First task', payload: { steps, revision: 1 } },
      { event_type: 'plan.updated', assistant_name: 'Reader', delegated_task: 'Second task', payload: { steps, revision: 1 } },
    ])
    expect(wrapper.findAll('.copilot-runtime-card__row--plan strong').map((row) => row.text())).toEqual([
      '1. Search', '1.1. Read', '2. Search', '2.1. Read',
    ])
  })

  it('keeps legacy plan events attached to the same-source structured parent', () => {
    const wrapper = render([
      plan([{ id: 'root', title: 'Search', status: 'completed' }]),
      { eventType: 'workflow.updated', message: 'Legacy child', parentId: 'root' },
    ])
    expect(wrapper.findAll('.copilot-runtime-card__row--plan strong').map((row) => row.text())).toEqual([
      '1. Search', '1.1. Legacy child',
    ])
  })

  it.each(['completed', 'failed', 'skipped'])('does not time a %s step with no reliable end timestamp', async (status) => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-23T00:00:10Z'))
    const started = plan([{ id: 'one', title: 'Search', status: 'in_progress' }], '2026-11-23T00:00:00Z')
    const wrapper = render([started], true)
    expect(wrapper.find('.copilot-timeline-time').text()).toBe('10s')
    const finished = plan([{ id: 'one', title: 'Search', status }], undefined, 2)
    await wrapper.setProps({ steps: [started, finished] })
    expect(wrapper.find('.copilot-timeline-time').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(10_000)
    expect(wrapper.find('.copilot-timeline-time').exists()).toBe(false)
    // A later upstream terminal observation can still close the known start.
    await wrapper.setProps({ steps: [
      started, finished,
      { ...finished, observedAt: '2026-11-23T00:00:20Z' },
    ] })
    expect(wrapper.find('.copilot-timeline-time').text()).toBe('20s')
    await vi.advanceTimersByTimeAsync(10_000)
    expect(wrapper.find('.copilot-timeline-time').text()).toBe('20s')
  })
})
