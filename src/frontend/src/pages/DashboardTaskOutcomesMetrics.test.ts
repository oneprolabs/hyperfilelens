import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const dashboard = readFileSync(resolve(process.cwd(), 'src/pages/Dashboard.vue'), 'utf8')
const dashboardApi = readFileSync(resolve(process.cwd(), 'src/lib/dashboardApi.ts'), 'utf8')
const tasks = readFileSync(resolve(process.cwd(), 'src/pages/ops/Tasks.vue'), 'utf8')

describe('Dashboard task outcome metrics', () => {
  it('aggregates each day from terminal tasks completed during that day', () => {
    expect(dashboardApi).toContain('function taskOutcomeDayRanges()')
    expect(dashboardApi).toContain('finished_after: range.finishedAfter')
    expect(dashboardApi).toContain('finished_before: range.finishedBefore')
    expect(dashboardApi).toContain("terminal_only: 'true'")
    expect(dashboardApi).not.toContain("listTasks({ time_range: '7d', page_size: 500 })")
  })

  it('opens a matching completed-task range when a chart day is selected', () => {
    expect(dashboard).toContain("time_field: 'finished'")
    expect(dashboard).toContain('@click="openTaskOutcomes(day)"')
    expect(tasks).toContain("time_field: textQueryValue(route.query.time_field) === 'finished' ? 'finished' : 'created'")
    expect(tasks).toContain("terminal_only: textQueryValue(route.query.terminal_only) === 'true'")
    expect(tasks).toContain('finished_after: timeParams.finished_after')
  })

  it('shows hover details in a popover without reserving chart space', () => {
    expect(dashboard).toContain('<HflPopover')
    expect(dashboard).toContain('popper-class="dashboard-chart-outcome-popper"')
    expect(dashboard).toContain(':global(.dashboard-chart-outcome-popper.el-popper)')
    expect(dashboard).toContain('chart-inline-tip__metrics')
    expect(dashboard).toContain("t('dashboard.chartSuccessRate')")
    expect(dashboard).toContain('.chart-inline-tip__metric--cancel {\n  color: #64748b;')
    expect(dashboard).toContain('--dashboard-success: var(--color-success);')
    expect(dashboard).toContain('--dashboard-error: var(--color-error);')
    expect(dashboard).not.toContain('.chart-status {')
  })
})
