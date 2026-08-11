import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const styles = readFileSync(resolve(process.cwd(), 'src/styles/ops-list-ui.css'), 'utf8')
const incidentsPage = readFileSync(resolve(process.cwd(), 'src/pages/ops/AlertIncidents.vue'), 'utf8')
const accountStyles = readFileSync(resolve(process.cwd(), 'src/platform-ops/styles/accountManagement.css'), 'utf8')
const monitoringStyles = readFileSync(resolve(process.cwd(), 'src/platform-ops/styles/monitoring.css'), 'utf8')

describe('operations statistics responsive layout', () => {
  it('preserves desktop styles and only compacts grids on phones', () => {
    expect(styles).toMatch(/\.hfl-ops-page\s*{[^}]*gap:\s*24px;/s)
    expect(styles).not.toContain('@media (min-width: 768px) and (max-width: 1279.98px)')
    expect(styles).toMatch(/@media \(max-width: 767\.98px\)[\s\S]*?\.hfl-ops-page\s*{[^}]*gap:\s*12px;/)
    expect(styles).toMatch(/@media \(max-width: 767\.98px\)[\s\S]*?\.hfl-ops-stats-grid--2,[\s\S]*?\.hfl-ops-stats-grid--5\s*{[^}]*grid-template-columns:\s*repeat\(2,/)
    expect(styles).toContain('.hfl-ops-stats-grid--3 > :last-child:nth-child(odd)')
    expect(styles).toContain('.hfl-ops-stats-grid--5 > :last-child:nth-child(odd)')
    expect(styles).toMatch(/@media \(min-width: 1024px\)[\s\S]*?\.hfl-ops-stats-grid--5\s*{[^}]*grid-template-columns:\s*repeat\(5,/)
  })

  it('lets the incidents page scroll naturally on phones', () => {
    expect(incidentsPage).toContain('hfl-ops-page hfl-ops-page--fill alert-incidents-page')
    expect(styles).toMatch(/\.alert-incidents-page\s*{[^}]*overflow-y:\s*auto;/s)
    expect(styles).toMatch(/\.alert-incidents-page > \.hfl-list-panel--fill\s*{[^}]*flex:\s*0 0 auto;[^}]*overflow:\s*visible;/s)
  })

  it('keeps platform operations overrides on the shared two-column mobile grid', () => {
    expect(accountStyles).toMatch(/\.platform-account-page \.hfl-ops-stats-grid--4\s*{[^}]*repeat\(2,/s)
    expect(monitoringStyles).toMatch(/\.platform-monitoring-page \.hfl-ops-stats-grid--4\s*{[^}]*repeat\(2,/s)
  })
})
