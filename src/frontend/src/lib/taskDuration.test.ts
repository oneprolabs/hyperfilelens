import { describe, expect, it } from 'vitest'
import { elapsedTaskTime, totalTaskDuration } from './taskDuration'

const start = '2026-09-29T07:58:49.000Z'
const task = { status: 'running', started_at: start, finished_at: null }

describe('task duration', () => {
  it('advances from the real start time and catches up after a suspended tab', () => {
    expect(totalTaskDuration(task, Date.parse(start) + 10_000)).toBe('00:00:10')
    expect(totalTaskDuration(task, Date.parse(start) + 125_000)).toBe('00:02:05')
    expect(totalTaskDuration(task, Date.parse(start) + 6_657_000)).toBe('01:50:57')
  })

  it('shows days with minute precision and retains seconds in the exact value', () => {
    const now = Date.parse(start) + (86_400 + 2 * 3600 + 9 * 60 + 31) * 1000
    expect(totalTaskDuration(task, now)).toBe('1d 02:09')
    expect(totalTaskDuration(task, now, true)).toBe('1d 02:09:31')
    expect(totalTaskDuration(task, now + 60_000)).toBe('1d 02:10')
    expect(totalTaskDuration(task, Date.parse(start) + 12 * 86_400_000 + 3_000)).toBe('12d 00:00')
    expect(totalTaskDuration(task, Date.parse(start) + 86_399_000)).toBe('23:59:59')
    expect(totalTaskDuration(task, Date.parse(start) + 86_400_000)).toBe('1d 00:00')
  })

  it('freezes on the server finish time regardless of the client clock', () => {
    const finished = { ...task, status: 'success', finished_at: '2026-09-29T08:00:49.000Z' }
    expect(totalTaskDuration(finished, Date.parse(start) + 150_000)).toBe('00:02:00')
    expect(totalTaskDuration(finished, Date.parse(start) + 500_000)).toBe('00:02:00')
  })

  it('does not count queue time or invent a start time', () => {
    expect(totalTaskDuration({ ...task, status: 'waiting' }, Date.parse(start) + 60_000)).toBeNull()
    expect(totalTaskDuration({ ...task, status: 'blocked' }, Date.parse(start) + 60_000)).toBeNull()
    expect(totalTaskDuration({ ...task, started_at: null }, Date.parse(start) + 60_000)).toBeNull()
    expect(totalTaskDuration({ ...task, status: 'failed' }, Date.parse(start) + 60_000)).toBeNull()
  })

  it('rejects invalid dates and never displays negative time', () => {
    expect(elapsedTaskTime('invalid', start)).toBeNull()
    expect(totalTaskDuration(task, Number.NaN)).toBeNull()
    expect(totalTaskDuration(task, Date.parse(start) - 10_000)).toBe('00:00:00')
    expect(elapsedTaskTime(start, '2026-09-29T07:58:48.000Z')).toBeNull()
  })

  it('formats step durations from explicit start and end timestamps', () => {
    expect(elapsedTaskTime(start, '2026-09-29T07:58:58.000Z')).toBe('9s')
  })
})
