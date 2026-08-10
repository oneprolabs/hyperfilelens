import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const dashboard = readFileSync(resolve(process.cwd(), 'src/pages/Dashboard.vue'), 'utf8')
const appShell = readFileSync(resolve(process.cwd(), 'src/app/layout/AppShell.vue'), 'utf8')

describe('Dashboard pipeline responsive layout', () => {
  it('keeps the desktop pipeline and adds compact tablet and phone layouts', () => {
    expect(dashboard).toContain('@media (min-width: 1024px)')
    expect(dashboard).toContain('@media (min-width: 768px) and (max-width: 1023.98px)')
    expect(dashboard).toContain('@media (max-width: 767.98px)')
    expect(dashboard).toContain('grid-template-columns: minmax(0, 1fr) 24px minmax(0, 1fr) 24px minmax(0, 1fr)')
    expect(dashboard).toContain('.pipeline__connector-line::after')
    expect(dashboard).toContain('@media (max-width: 479.98px)')
    expect(dashboard).toContain('min-height: 38px')
    expect(dashboard).toContain('@media (max-width: 1023.98px) and (prefers-reduced-motion: reduce)')
  })

  it('matches the route skeleton to the responsive pipeline', () => {
    expect(appShell).toContain('@media (min-width: 768px) and (max-width: 1023.98px)')
    expect(appShell).toContain('@media (max-width: 767.98px)')
    expect(appShell).toContain('.app-route-dashboard-skeleton__pipeline-step')
    expect(appShell).toContain('grid-template-columns: minmax(0, 1fr) 24px minmax(0, 1fr) 24px minmax(0, 1fr)')
    expect(appShell).toContain('margin: 32px 0 0')
    expect(appShell).toContain('height: 38px')
  })

  it('uses the canonical production-source availability summary', () => {
    expect(dashboard).toContain('overview?.productionSources.total')
    expect(dashboard).toContain('overview?.productionSources.available')
    expect(dashboard).toContain('overview?.productionSources.unavailable')
    expect(dashboard).not.toContain('overview?.sourceActive')
  })
})
