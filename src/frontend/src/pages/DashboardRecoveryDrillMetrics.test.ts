import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const dashboard = readFileSync(resolve(process.cwd(), 'src/pages/Dashboard.vue'), 'utf8')
const dashboardApi = readFileSync(resolve(process.cwd(), 'src/lib/dashboardApi.ts'), 'utf8')
const tasks = readFileSync(resolve(process.cwd(), 'src/pages/ops/Tasks.vue'), 'utf8')

describe('Dashboard recovery drill metrics', () => {
  it('limits recovery drill metrics to restores created in the last 24 hours', () => {
    expect(dashboardApi).toContain("task_type: 'restore', created_after: recoveryDrillStartedAfter")
    expect(dashboardApi).toContain('recoveryDrill24h: summarizeRecoveryDrill24h(recoveryDrill24hStats)')
    expect(dashboard).not.toContain('overview?.taskStats.running')
  })

  it('opens the task list with the matching recovery drill filters', () => {
    expect(dashboard).toContain("tasks: '/ops/task?task_type=restore&time_mode=24h'")
    expect(tasks).toContain("task_type: textQueryValue(route.query.task_type)")
    expect(tasks).toContain("time_mode: textQueryValue(route.query.time_mode) === '24h' ? '24h' : '7d'")
    expect(tasks).toContain('stats.value = await taskStatistics(filterParams, { signal })')
  })
})
