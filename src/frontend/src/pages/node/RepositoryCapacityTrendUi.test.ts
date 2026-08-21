import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

const page = readFileSync(new URL('./Repositories.vue', import.meta.url), 'utf8')
const component = readFileSync(
  new URL('../../components/RepositoryCapacityTrend.vue', import.meta.url),
  'utf8',
)

describe('repository monitor detail tab', () => {
  it('shows Monitor for every repository type immediately before Tasks', () => {
    expect(page).toContain("name=\"monitor\"")
    expect(page).toContain('<RepositoryCapacityTrend')
    const monitorName = page.indexOf('name="monitor"')
    const tasksName = page.indexOf('name="tasks"')
    const monitorClose = page.indexOf('</ElTabPane>', monitorName)
    expect(monitorName).toBeLessThan(tasksName)
    expect(page.indexOf('<ElTabPane', monitorClose)).toBe(page.lastIndexOf('<ElTabPane', tasksName))
  })

  it('keeps missing capacity points disconnected and does not chart object count', () => {
    expect(component).toContain('connectNulls: false')
    expect(component).toContain('showSymbol: validPoints.value.length === 1')
    expect(component).not.toContain('object_count')
    expect(component).not.toContain('capacityTrendTitle')
    expect(component).not.toContain('capacityTrendEstimatedNote')
    expect(component).not.toContain('capacityGrowth')
  })
})
