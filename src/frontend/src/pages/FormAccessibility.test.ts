import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const dashboard = readFileSync(resolve(process.cwd(), 'src/pages/Dashboard.vue'), 'utf8')
const usage = readFileSync(resolve(process.cwd(), 'src/pages/insight/InsightUsage.vue'), 'utf8')

describe('form accessibility regressions', () => {
  it('associates Dashboard capacity labels with named number inputs', () => {
    expect(dashboard).toContain('for="dashboard-capacity-plan-amount"')
    expect(dashboard).toContain('id="dashboard-capacity-plan-amount"')
    expect(dashboard).toContain('name="dashboard_capacity_plan_amount"')
    expect(dashboard).toContain('for="dashboard-capacity-plan-factor"')
    expect(dashboard).toContain('id="dashboard-capacity-plan-factor"')
    expect(dashboard).toContain('name="dashboard_capacity_plan_factor"')
  })

  it('names both Usage date inputs and the question search field', () => {
    expect(usage).toContain(":id=\"['usage-start-date', 'usage-end-date']\"")
    expect(usage).toContain(":name=\"['usage_start_date', 'usage_end_date']\"")
    expect(usage).toContain('id="usage-question-search"')
    expect(usage).toContain('name="usage_question_search"')
    expect(usage).toContain(":aria-label=\"t('insight.usage.searchAria')\"")
  })
})
