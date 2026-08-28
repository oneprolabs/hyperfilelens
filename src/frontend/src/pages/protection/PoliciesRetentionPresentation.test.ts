import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const policiesPage = readFileSync(resolve(process.cwd(), 'src/pages/protection/Policies.vue'), 'utf8')
const detailPage = readFileSync(
  resolve(process.cwd(), 'src/pages/protection/components/PolicyDetailEditorForm.vue'),
  'utf8',
)

describe('Backup Policies retention presentation', () => {
  it.each([policiesPage, detailPage])('uses the same time-window retention copy', (source) => {
    expect(source).toContain('protection.policiesPage.retentionLatestMany')
    expect(source).toContain('protection.policiesPage.shortDesc')
    expect(source).toContain('protection.policiesPage.midDesc')
    expect(source).toContain('protection.policiesPage.longDesc')
  })

  it('renders retention time-window rules as full lines without tier labels', () => {
    expect(policiesPage).toContain('policy-retention-detail-list__line--full')
    expect(detailPage).toContain('policy-detail-overview__retention-line--full')
  })
})
