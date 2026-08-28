import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const policiesPage = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/Policies.vue'),
  'utf8',
)

describe('Backup Policies schedule popover', () => {
  it('uses the retention popover structure and localized schedule labels', () => {
    expect(policiesPage).toContain('policyScheduleListSummary(row)')
    expect(policiesPage).toContain('policyScheduleDetailLines(row)')
    expect(policiesPage).toContain('popper-class="policy-retention-popover"')
    expect(policiesPage).toContain("t('protection.policiesPage.scheduleCycle')")
    expect(policiesPage).toContain("t('protection.policiesPage.scheduleTimezone')")
    expect(policiesPage).toContain("t('protection.policiesPage.scheduleStartsAt')")
  })

  it('localizes all structured quick-schedule values', () => {
    expect(policiesPage).toContain("t(`protection.policiesPage.${key}`)")
    expect(policiesPage).toContain("'protection.policiesPage.unitMinutes'")
    expect(policiesPage).toContain("t('protection.policiesPage.scheduleMonthEnd')")
  })
})
